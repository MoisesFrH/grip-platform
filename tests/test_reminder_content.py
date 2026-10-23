"""
Reminder text, notes summary validation and payment-reply detection (no
database needed). Run with:

    .venv/bin/python -m pytest tests/test_reminder_content.py
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import appointments as ap
from app.services import reminder_summary as rs


def _appt(hour_utc=19, day=24):
    return SimpleNamespace(scheduled_at=datetime(2026, 10, day, hour_utc, 0, tzinfo=timezone.utc), therapist_name="María Ruiz")


def test_when_uses_clinic_time_and_spanish_date() -> None:
    now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    assert ap._format_when(_appt().scheduled_at, now=now) == "el sábado 24 de octubre a las 3:00 p. m."


def test_when_says_manana_and_hoy() -> None:
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    assert ap._format_when(_appt().scheduled_at, now=now) == "mañana a las 3:00 p. m."
    assert ap._format_when(_appt(hour_utc=22, day=23).scheduled_at, now=now) == "hoy a las 6:00 p. m."


def test_message_has_summary_payment_question_and_confirmation() -> None:
    content = ap.ReminderContent("Ana", "mañana a las 3:00 p. m.", "María Ruiz", "la comunicación y la atención")
    text = content.text()
    assert "Hola Ana" in text and "María Ruiz" in text
    assert "vamos a trabajar en la comunicación y la atención." in text
    assert "PAGAR" in text and "transferencia" in text
    assert '"Sí"' in text


def test_template_params_have_no_newlines() -> None:
    content = ap.ReminderContent(None, "mañana", "Dra.\nRuiz", "trabajar   la\natención")
    params = content.template_params()
    assert params[0] == "paciente"
    assert all("\n" not in p and "  " not in p for p in params)
    assert len(params) == 4


def test_generic_summary_when_none() -> None:
    content = ap.ReminderContent(None, "mañana", "X", rs.GENERIC_SUMMARY)
    assert "objetivos que acordamos" in content.text()


def test_summary_accepts_a_good_sentence() -> None:
    out = rs.summarize_notes(["trabajar turnos de conversación"], generate=lambda p: "Trabajaremos en la comunicación y la atención.")
    assert out == "la comunicación y la atención"
    assert rs.summarize_notes(["x"], generate=lambda p: "La atención.") == "la atención"


def test_summary_prompt_gets_notes_only_no_identifiers() -> None:
    seen = []
    rs.summarize_notes(["nota uno", "nota dos"], generate=lambda p: seen.append(p) or "la atención")
    assert "nota uno" in seen[0] and "nota dos" in seen[0]


def test_summary_fails_closed() -> None:
    assert rs.summarize_notes([], generate=lambda p: "la atención") is None
    assert rs.summarize_notes(["a"], generate=lambda p: None) is None
    assert rs.summarize_notes(["a"], generate=lambda p: "NINGUNO") is None
    assert rs.summarize_notes(["a"], generate=lambda p: "3 veces por semana") is None  # digits
    assert rs.summarize_notes(["a"], generate=lambda p: "palabra " * 50) is None  # too long
    assert rs.summarize_notes(["a"], generate=lambda p: "Una. Dos oraciones.") is None

    def boom(prompt):
        raise RuntimeError("down")

    assert rs.summarize_notes(["a"], generate=boom) is None


def test_summary_can_be_disabled_per_tenant() -> None:
    assert rs.summary_enabled(SimpleNamespace(config={"reminders": {"include_summary": False}})) is False
    assert rs.summary_enabled(SimpleNamespace(config={})) is True


def test_payment_keywords() -> None:
    assert ap.wants_to_pay("PAGAR")
    assert ap.wants_to_pay("sí, quiero pagar antes")
    assert ap.wants_to_pay("pásame los datos de la cuenta")
    assert not ap.wants_to_pay("no puedo ir, voy a cancelar y pagar otro día")
    assert not ap.wants_to_pay("ya pagué")
    assert ap.says_already_paid("Ya pagué, te mando el comprobante")
    assert not ap.says_already_paid("sí")


def test_payment_message_uses_fictitious_default_and_tenant_override() -> None:
    text = ap.payment_details_message(SimpleNamespace(config={}))
    assert "Banco Ficticio de Prueba" in text and "000-000000-0" in text and "prueba" in text.lower()
    real = ap.payment_details_message(SimpleNamespace(config={"payment": {
        "bank_name": "Banco Real", "account_holder": "ND", "account_number": "123", "fictitious": False}}))
    assert "Banco Real" in real and "123" in real and "prueba" not in real.lower()