"""
Pure-logic tests for the safety/risk detection layer — no network, no
Twilio, no Gemini needed. Run with:

    .venv/bin/python -m tests.test_safety
"""

from app.core.db import SessionLocal
from app.models.tenant import Tenant
from app.services.safety import SafetyCategory, SafetyPriority, check_message
from app.services.safety_protocol import resolve_protocol_action


def _grip() -> Tenant:
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == "grip").one()
    db.close()
    return tenant


def test_no_risk_message_passes_through() -> None:
    tenant = _grip()
    result = check_message(tenant, "Hola, quiero saber los horarios de atención")
    assert result.risk_detected is False
    assert result.category is None
    print("PASS: test_no_risk_message_passes_through")


def test_self_harm_detected_case_and_accent_insensitive() -> None:
    tenant = _grip()
    # Deliberately mixed case + missing accent, as a real WhatsApp message would be.
    result = check_message(tenant, "ya no quiero SEGUIR viviendo, quiero hacerme dano")
    assert result.risk_detected is True
    assert result.category == SafetyCategory.SUICIDE_OR_SELF_HARM
    assert result.priority == SafetyPriority.CRITICAL
    assert result.requires_immediate_human is True
    print("PASS: test_self_harm_detected_case_and_accent_insensitive")


def test_symptoms_detected_as_high_not_critical() -> None:
    tenant = _grip()
    result = check_message(tenant, "tengo ansiedad y no puedo dormir hace días")
    assert result.risk_detected is True
    assert result.category == SafetyCategory.SYMPTOMS
    assert result.priority == SafetyPriority.HIGH
    assert result.requires_immediate_human is False
    print("PASS: test_symptoms_detected_as_high_not_critical")


def test_most_severe_category_wins_when_multiple_match() -> None:
    tenant = _grip()
    # Touches both SYMPTOMS ("tengo ansiedad") and SUICIDE_OR_SELF_HARM
    # ("no quiero vivir") — must resolve to the more severe one.
    result = check_message(tenant, "tengo ansiedad y ya no quiero vivir")
    assert result.category == SafetyCategory.SUICIDE_OR_SELF_HARM
    assert result.priority == SafetyPriority.CRITICAL
    print("PASS: test_most_severe_category_wins_when_multiple_match")


def test_tenant_extra_keyword_is_picked_up() -> None:
    tenant = _grip()
    result = check_message(tenant, "mi hija se quiere ir de la casa y no sabemos donde esta")
    assert result.risk_detected is True
    assert result.category == SafetyCategory.IMMEDIATE_RISK
    print("PASS: test_tenant_extra_keyword_is_picked_up")


def test_protocol_resolution_uses_tenant_config_when_present() -> None:
    tenant = _grip()
    action = resolve_protocol_action(tenant, SafetyCategory.SUICIDE_OR_SELF_HARM, SafetyPriority.CRITICAL)
    assert action.configured is True
    assert "guardia de GRIP" in action.patient_message
    assert action.notify == ["on_call_psychologist", "clinical_director"]
    print("PASS: test_protocol_resolution_uses_tenant_config_when_present")


def test_protocol_resolution_falls_back_generically_when_unconfigured() -> None:
    tenant = _grip()
    # ABUSE has no tenant-specific action configured in the seed.
    action = resolve_protocol_action(tenant, SafetyCategory.ABUSE, SafetyPriority.CRITICAL)
    assert action.configured is False
    assert "equipo" in action.patient_message
    assert "ABUSE" in action.handoff_note
    assert action.notify == []
    print("PASS: test_protocol_resolution_falls_back_generically_when_unconfigured")


def test_empty_message_is_never_flagged() -> None:
    tenant = _grip()
    assert check_message(tenant, "").risk_detected is False
    assert check_message(tenant, "   ").risk_detected is False
    print("PASS: test_empty_message_is_never_flagged")


if __name__ == "__main__":
    test_no_risk_message_passes_through()
    test_self_harm_detected_case_and_accent_insensitive()
    test_symptoms_detected_as_high_not_critical()
    test_most_severe_category_wins_when_multiple_match()
    test_tenant_extra_keyword_is_picked_up()
    test_protocol_resolution_uses_tenant_config_when_present()
    test_protocol_resolution_falls_back_generically_when_unconfigured()
    test_empty_message_is_never_flagged()
    print("\nAll safety layer tests passed.")
