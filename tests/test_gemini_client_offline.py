"""
Offline verification of GeminiClient's orchestration logic (forced tool
calling, restricted tool names, grounded second turn) against a fake
genai client. This sandbox has no network access to the live Gemini API
(generativelanguage.googleapis.com is blocked), so this is what stands in
for a real call in CI/local dev without a network-reachable key: it proves
OUR control flow is correct, not that the live API behaves as documented.

Run with:
    .venv/bin/python -m tests.test_gemini_client_offline
"""

from dataclasses import dataclass, field
from typing import Any

from google.genai import types

from app.core.db import SessionLocal
from app.models.tenant import Tenant
from app.services.gemini_client import GeminiClient, UnsupportedIntentError
from app.services.tools import INTENT_TOOL_MAP, run_tool


@dataclass
class FakeCall:
    name: str
    args: dict[str, Any]


@dataclass
class FakeUsage:
    prompt_token_count: int
    candidates_token_count: int
    total_token_count: int


@dataclass
class FakeCandidate:
    content: Any


@dataclass
class FakeResponse:
    function_calls: list[FakeCall] | None = None
    text: str | None = None
    usage_metadata: FakeUsage | None = None
    candidates: list[FakeCandidate] = field(default_factory=lambda: [FakeCandidate(content=None)])


class FakeModels:
    """Records every call it receives so the test can assert on config,
    and scripts a forced function call on turn 1, plain text on turn 2."""

    def __init__(self, tool_name: str, final_text: str) -> None:
        self.tool_name = tool_name
        self.final_text = final_text
        self.calls: list[dict] = []

    def generate_content(self, *, model: str, contents, config: types.GenerateContentConfig):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if len(self.calls) == 1:
            return FakeResponse(function_calls=[FakeCall(name=self.tool_name, args={})])
        return FakeResponse(
            text=self.final_text,
            usage_metadata=FakeUsage(prompt_token_count=42, candidates_token_count=13, total_token_count=55),
        )


class FakeGenaiClient:
    def __init__(self, tool_name: str, final_text: str) -> None:
        self.models = FakeModels(tool_name, final_text)


def test_forces_restricted_tool_call_and_grounds_answer() -> None:
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == "grip").one()

    fake_client = FakeGenaiClient(tool_name="get_location", final_text="Estamos en Quito, cerca del metro.")
    client = GeminiClient(client=fake_client)

    result = client.answer(tenant=tenant, intent="LOCATION", user_message="¿Dónde quedan?")

    # --- turn 1 was forced into ANY mode restricted to LOCATION's tools ---
    first_config = fake_client.models.calls[0]["config"]
    fc_config = first_config.tool_config.function_calling_config
    assert fc_config.mode == types.FunctionCallingConfigMode.ANY
    assert fc_config.allowed_function_names == INTENT_TOOL_MAP["LOCATION"]

    # --- the real tool ran against the real tenant config ---
    assert result.tools_used == ["get_location"]
    assert result.tool_results["get_location"] == run_tool("get_location", tenant)
    assert result.tool_results["get_location"]["configured"] is True

    # --- turn 2 was forced to NONE (no chained tool calls) ---
    second_config = fake_client.models.calls[1]["config"]
    assert second_config.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.NONE

    # --- final answer + usage came through untouched ---
    assert result.text == "Estamos en Quito, cerca del metro."
    assert result.usage == {"prompt_tokens": 42, "response_tokens": 13, "total_tokens": 55}

    db.close()
    print("PASS: test_forces_restricted_tool_call_and_grounds_answer")


def test_unmapped_intent_is_rejected_before_calling_the_model() -> None:
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == "grip").one()

    fake_client = FakeGenaiClient(tool_name="irrelevant", final_text="irrelevant")
    client = GeminiClient(client=fake_client)

    try:
        client.answer(tenant=tenant, intent="CLINICAL_QUESTION", user_message="tengo un dolor")
    except UnsupportedIntentError:
        pass
    else:
        raise AssertionError("expected UnsupportedIntentError for a category-C intent")

    assert fake_client.models.calls == [], "must not call Gemini at all for an unmapped intent"

    db.close()
    print("PASS: test_unmapped_intent_is_rejected_before_calling_the_model")


def test_therapist_name_argument_is_forwarded_to_the_tool() -> None:
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == "grip").one()

    fake_client = FakeGenaiClient(tool_name="get_therapist_availability", final_text="Andrés está con disponibilidad limitada.")
    # Override args on the scripted call to simulate Gemini extracting the name.
    fake_client.models.generate_content_orig = fake_client.models.generate_content

    def scripted(*, model, contents, config):
        if len(fake_client.models.calls) == 0:
            fake_client.models.calls.append({"model": model, "contents": contents, "config": config})
            return FakeResponse(
                function_calls=[FakeCall(name="get_therapist_availability", args={"therapist_name": "Andrés Salas"})]
            )
        fake_client.models.calls.append({"model": model, "contents": contents, "config": config})
        return FakeResponse(
            text="Andrés está con disponibilidad limitada.",
            usage_metadata=FakeUsage(1, 1, 2),
        )

    fake_client.models.generate_content = scripted

    client = GeminiClient(client=fake_client)
    result = client.answer(
        tenant=tenant, intent="THERAPIST_AVAILABILITY", user_message="¿Cómo está Andrés Salas de disponibilidad?"
    )

    assert result.tool_results["get_therapist_availability"]["therapists"][0]["name"] == "Andrés Salas"
    assert result.tool_results["get_therapist_availability"]["therapists"][0]["availability"] == "🟡"

    db.close()
    print("PASS: test_therapist_name_argument_is_forwarded_to_the_tool")


if __name__ == "__main__":
    test_forces_restricted_tool_call_and_grounds_answer()
    test_unmapped_intent_is_rejected_before_calling_the_model()
    test_therapist_name_argument_is_forwarded_to_the_tool()
    print("\nAll offline Gemini client tests passed.")
