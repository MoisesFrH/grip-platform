"""
The only module allowed to call the Gemini API. Implements the shape from
architecture doc §9:

    User -> classifier -> intent -> tools -> structured result -> response

never the unsafe shape:

    User -> Gemini -> free response

Guardrails enforced here, not just described:

  1. `tool_config.function_calling_config.mode = ANY` — Gemini MUST call a
     function on the first turn; it cannot skip straight to prose.
  2. `allowed_function_names` is restricted to exactly the intent's tool
     set from INTENT_TOOL_MAP. An intent with no mapped tools raises
     instead of calling the model at all — there is no implicit fallback
     to "let Gemini answer anyway".
  3. The final answer is produced in a SECOND call whose only inputs are
     the conversation + the tool's own structured output (a function
     response part) — the model is grounded in what the backend actually
     returned, and mode is forced back to NONE so it cannot chain into
     more tool calls or drift into an ungrounded answer.
  4. The system instruction states the clinical/safety boundaries
     explicitly (no diagnosing, no treatment advice, no inventing data,
     no deciding availability), as a second layer behind the fact that
     the model has no tool capable of doing any of that anyway.
"""

from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import types

from app.core.config import get_settings
from app.models.tenant import Tenant
from app.services.tools import INTENT_TOOL_MAP, TOOL_SCHEMAS, run_tool

SYSTEM_INSTRUCTION = """\
Eres el asistente de WhatsApp de {business_name}. Respondes SIEMPRE en {language}.

Reglas estrictas, sin excepción:
- Solo puedes afirmar datos que provengan del resultado de una function call. \
Si el resultado indica que algo no está configurado, dilo con naturalidad \
("no tengo ese dato ahora mismo, te conecto con el equipo") y nunca lo inventes.
- No diagnosticas, no interpretas síntomas, no recomiendas tratamientos ni \
medicación, no realizas terapia, no dices qué terapeuta es clínicamente \
adecuado para alguien.
- No decides ni estimas la disponibilidad de un terapeuta: usa exactamente \
el valor que te da la herramienta.
- Sé breve, cálido y concreto, como un mensaje de WhatsApp real, no un email.
"""


class UnsupportedIntentError(Exception):
    """Raised when asked to answer an intent with no configured tool set —
    i.e. an intent that must never reach free generation (category B/C, or
    an intent typo). The caller should treat this as a bug, not a
    conversational fallback."""


@dataclass
class GeminiAnswer:
    text: str
    tools_used: list[str] = field(default_factory=list)
    tool_results: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int | None] = field(default_factory=dict)


class GeminiClient:
    def __init__(self, client: "genai.Client | None" = None) -> None:
        settings = get_settings()
        # Injectable for tests, so the orchestration logic (forced tool
        # calling, restricted tool names, grounded second turn) can be
        # verified offline against a fake client instead of the live API.
        self._client = client or genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.gemini_model

    def answer(
        self,
        *,
        tenant: Tenant,
        intent: str,
        user_message: str,
        history: list[dict[str, str]] | None = None,
    ) -> GeminiAnswer:
        allowed_tools = INTENT_TOOL_MAP.get(intent)
        if not allowed_tools:
            raise UnsupportedIntentError(
                f"Intent '{intent}' has no allowed tool set; it must not reach Gemini for "
                "free-form generation (category B/C intents are handled deterministically)."
            )

        tools = [types.Tool(function_declarations=[TOOL_SCHEMAS[name] for name in allowed_tools])]
        system_instruction = SYSTEM_INSTRUCTION.format(
            business_name=tenant.name, language=tenant.language
        )

        contents = _build_contents(history, user_message)

        # --- Turn 1: force a tool call, restricted to this intent's tools ---
        forced_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY,
                    allowed_function_names=allowed_tools,
                )
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        first = self._client.models.generate_content(
            model=self._model, contents=contents, config=forced_config
        )

        function_calls = first.function_calls or []
        if not function_calls:
            # Should not happen with mode=ANY; fail closed rather than
            # falling back to whatever text the model produced instead.
            raise RuntimeError(f"Gemini returned no function call for intent '{intent}'")

        tools_used: list[str] = []
        tool_results: dict[str, Any] = {}
        response_parts: list[types.Part] = []

        for call in function_calls:
            result = run_tool(call.name, tenant, **(call.args or {}))
            tools_used.append(call.name)
            tool_results[call.name] = result
            response_parts.append(
                types.Part.from_function_response(name=call.name, response=result)
            )

        # --- Turn 2: generate the grounded answer, no further tool calls ---
        follow_up_contents = contents + [
            first.candidates[0].content,
            types.Content(role="user", parts=response_parts),
        ]
        final_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.NONE
                )
            ),
        )
        final = self._client.models.generate_content(
            model=self._model, contents=follow_up_contents, config=final_config
        )

        usage = {}
        if final.usage_metadata:
            usage = {
                "prompt_tokens": final.usage_metadata.prompt_token_count,
                "response_tokens": final.usage_metadata.candidates_token_count,
                "total_tokens": final.usage_metadata.total_token_count,
            }

        return GeminiAnswer(
            text=final.text or "",
            tools_used=tools_used,
            tool_results=tool_results,
            usage=usage,
        )

    def classify_intent(self, *, candidate_intents: list[str], user_message: str) -> str:
        """
        Disambiguates among a BOUNDED set of intents the deterministic
        keyword pass already narrowed things down to (architecture doc
        §6/§8: hybrid classification). Gemini cannot invent a category
        outside `candidate_intents` — the enum constraint on the tool's
        own parameter makes that a schema violation, not just a prompt
        instruction. OTHER_UNCLEAR is always offered as an escape valve
        so an odd message doesn't get forced into the wrong bucket.

        Never called with CLINICAL_QUESTION or SAFETY_RISK as options —
        those are decided exclusively by the safety layer before this is
        ever reached (see intent_classifier.py).
        """
        options = list(dict.fromkeys([*candidate_intents, "OTHER_UNCLEAR"]))

        schema = {
            "name": "classify_intent",
            "description": "Classify the patient's WhatsApp message into exactly one of the given intents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "enum": options,
                        "description": "The single best-matching intent for this message.",
                    }
                },
                "required": ["intent"],
            },
        }

        config = types.GenerateContentConfig(
            system_instruction=(
                "Clasifica el mensaje del paciente en exactamente una de las intenciones "
                "dadas. No inventes ninguna categoría fuera de la lista."
            ),
            tools=[types.Tool(function_declarations=[schema])],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY,
                    allowed_function_names=["classify_intent"],
                )
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        response = self._client.models.generate_content(
            model=self._model,
            contents=[types.Content(role="user", parts=[types.Part(text=user_message)])],
            config=config,
        )

        calls = response.function_calls or []
        if not calls:
            return "OTHER_UNCLEAR"

        chosen = (calls[0].args or {}).get("intent", "OTHER_UNCLEAR")
        return chosen if chosen in options else "OTHER_UNCLEAR"


def _build_contents(
    history: list[dict[str, str]] | None, user_message: str
) -> list[types.Content]:
    contents: list[types.Content] = []
    for turn in history or []:
        role = "model" if turn.get("role") == "bot" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=turn["text"])]))
    contents.append(types.Content(role="user", parts=[types.Part(text=user_message)]))
    return contents
