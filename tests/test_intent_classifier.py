"""
Offline tests for the hybrid intent classifier — no network needed for the
deterministic/safety paths; the Gemini-disambiguation path uses the same
fake-client pattern as tests/test_gemini_client_offline.py.

Run with:
    .venv/bin/python -m tests.test_intent_classifier
"""

from dataclasses import dataclass, field
from typing import Any

from app.core.db import SessionLocal
from app.models.tenant import Tenant
from app.services.intent_classifier import classify_intent
from app.services.intents import Intent, IntentCategory, IntentPriority


def _grip() -> Tenant:
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == "grip").one()
    db.close()
    return tenant


def test_clean_single_keyword_match_skips_gemini_entirely() -> None:
    tenant = _grip()

    class ExplodingGeminiClient:
        def classify_intent(self, **kwargs):
            raise AssertionError("Gemini must not be called for an unambiguous message")

    result = classify_intent(tenant, "hola, ¿cómo llego al consultorio?", gemini_client=ExplodingGeminiClient())

    assert result.primary_intent == Intent.LOCATION
    assert result.source == "deterministic"
    assert result.category == IntentCategory.A
    assert result.requires_handoff is False
    print("PASS: test_clean_single_keyword_match_skips_gemini_entirely")


def test_safety_signal_overrides_everything_with_secondary_intent() -> None:
    tenant = _grip()

    class ExplodingGeminiClient:
        def classify_intent(self, **kwargs):
            raise AssertionError("safety override must short-circuit before Gemini is ever reached")

    # Brief §15's exact scenario: an appointment change + a self-harm signal
    # in the same message. Safety must win as primary; the admin request
    # survives only as secondary.
    result = classify_intent(
        tenant,
        "necesito cambiar mi cita y ya no quiero seguir viviendo",
        gemini_client=ExplodingGeminiClient(),
    )

    assert result.primary_intent == Intent.SAFETY_RISK
    assert result.priority == IntentPriority.CRITICAL
    assert result.requires_handoff is True
    assert result.source == "safety"
    assert result.secondary_intent == Intent.APPOINTMENT_CHANGE
    print("PASS: test_safety_signal_overrides_everything_with_secondary_intent")


def test_clinical_high_priority_without_critical_signal() -> None:
    tenant = _grip()
    result = classify_intent(tenant, "tengo ansiedad y no puedo dormir", gemini_client=None)

    assert result.primary_intent == Intent.CLINICAL_QUESTION
    assert result.priority == IntentPriority.HIGH
    assert result.source == "safety"
    print("PASS: test_clinical_high_priority_without_critical_signal")


@dataclass
class FakeCall:
    name: str
    args: dict[str, Any]


@dataclass
class FakeResponse:
    function_calls: list[FakeCall] | None = None


class FakeModelsForClassification:
    def __init__(self, chosen_intent: str) -> None:
        self.chosen_intent = chosen_intent
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return FakeResponse(function_calls=[FakeCall(name="classify_intent", args={"intent": self.chosen_intent})])


class FakeGenaiClientForClassification:
    def __init__(self, chosen_intent: str) -> None:
        self.models = FakeModelsForClassification(chosen_intent)


def test_ambiguous_message_is_bounded_to_real_candidates_via_gemini() -> None:
    from google.genai import types

    from app.services.gemini_client import GeminiClient

    tenant = _grip()
    # Touches both PAYMENT_INFO ("cuanto cuesta") and BOOKING_GUIDANCE
    # ("como reservo") keywords -> ambiguous -> Gemini disambiguates.
    fake_genai = FakeGenaiClientForClassification(chosen_intent="PAYMENT_INFO")
    gemini_client = GeminiClient(client=fake_genai)

    result = classify_intent(tenant, "cuanto cuesta una sesion y como reservo", gemini_client=gemini_client)

    assert result.primary_intent == Intent.PAYMENT_INFO
    assert result.source == "gemini"

    sent_config = fake_genai.models.calls[0]["config"]
    fc_config = sent_config.tool_config.function_calling_config
    assert fc_config.mode == types.FunctionCallingConfigMode.ANY
    sent_schema = sent_config.tools[0].function_declarations[0]
    allowed_enum = sent_schema.parameters.properties["intent"].enum
    # Bounded to the two real candidates + the OTHER_UNCLEAR escape valve —
    # never the full intent menu, and never CLINICAL_QUESTION/SAFETY_RISK.
    assert set(allowed_enum) == {"PAYMENT_INFO", "BOOKING_GUIDANCE", "OTHER_UNCLEAR"}
    print("PASS: test_ambiguous_message_is_bounded_to_real_candidates_via_gemini")


def test_zero_match_message_offers_full_non_clinical_menu() -> None:
    from app.services.gemini_client import GeminiClient
    from app.services.intents import CLASSIFIABLE_INTENTS

    tenant = _grip()
    fake_genai = FakeGenaiClientForClassification(chosen_intent="OTHER_UNCLEAR")
    gemini_client = GeminiClient(client=fake_genai)

    result = classify_intent(tenant, "buenas tardes", gemini_client=gemini_client)

    assert result.primary_intent == Intent.OTHER_UNCLEAR
    assert result.source == "gemini"

    sent_config = fake_genai.models.calls[0]["config"]
    sent_schema = sent_config.tools[0].function_declarations[0]
    allowed_enum = set(sent_schema.parameters.properties["intent"].enum)
    expected = {i.value for i in CLASSIFIABLE_INTENTS if i != Intent.OTHER_UNCLEAR} | {"OTHER_UNCLEAR"}
    assert allowed_enum == expected
    print("PASS: test_zero_match_message_offers_full_non_clinical_menu")


def test_gemini_error_fails_closed_to_other_unclear() -> None:
    tenant = _grip()

    class BrokenGeminiClient:
        def classify_intent(self, **kwargs):
            raise RuntimeError("network unreachable")

    result = classify_intent(tenant, "cuanto cuesta una sesion y como reservo", gemini_client=BrokenGeminiClient())

    assert result.primary_intent == Intent.OTHER_UNCLEAR
    assert result.source == "fallback_error"
    assert result.requires_handoff is True
    print("PASS: test_gemini_error_fails_closed_to_other_unclear")


def test_no_gemini_client_supplied_fails_closed() -> None:
    tenant = _grip()
    result = classify_intent(tenant, "buenas tardes", gemini_client=None)
    assert result.primary_intent == Intent.OTHER_UNCLEAR
    assert result.source == "fallback_no_client"
    print("PASS: test_no_gemini_client_supplied_fails_closed")


if __name__ == "__main__":
    test_clean_single_keyword_match_skips_gemini_entirely()
    test_safety_signal_overrides_everything_with_secondary_intent()
    test_clinical_high_priority_without_critical_signal()
    test_ambiguous_message_is_bounded_to_real_candidates_via_gemini()
    test_zero_match_message_offers_full_non_clinical_menu()
    test_gemini_error_fails_closed_to_other_unclear()
    test_no_gemini_client_supplied_fails_closed()
    print("\nAll intent classifier tests passed.")
