"""
The reminder-reply guard in orchestrator.process_inbound_message: a reply
to a pending appointment reminder that looks like a cancel/reschedule
request must always force a human handoff, without ever reaching Gemini
intent classification. Run with:

    .venv/bin/python -m pytest tests/test_appointment_reminders.py
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.core.db import SessionLocal
from app.models.appointment import Appointment, AppointmentStatus, ReminderResponse
from app.models.contact import Contact
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message
from app.models.tenant import Tenant
from app.services import appointments as appointments_service
from app.services.orchestrator import process_inbound_message


def _grip() -> Tenant:
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == "grip").one()
    db.close()
    return tenant


def _test_phone() -> str:
    return f"+1555{uuid.uuid4().int % 10_000_000:07d}"


def _cleanup(db, contact_id) -> None:
    db.query(Appointment).filter(Appointment.contact_id == contact_id).delete(synchronize_session=False)
    db.query(Message).filter(
        Message.conversation_id.in_(db.query(Conversation.id).filter(Conversation.contact_id == contact_id))
    ).delete(synchronize_session=False)
    db.query(Conversation).filter(Conversation.contact_id == contact_id).delete(synchronize_session=False)
    db.query(Contact).filter(Contact.id == contact_id).delete(synchronize_session=False)
    db.commit()


class _ExplodingGeminiClient:
    """Used to prove the reminder guard short-circuits BEFORE intent
    classification or Gemini ever get a chance to run."""

    def answer(self, **kwargs):
        raise AssertionError("Gemini.answer should not have been called")

    def classify_intent(self, **kwargs):
        raise AssertionError("classify_intent should not have been called")


def _seed_pending_reminder(db, tenant, phone: str) -> tuple[Contact, Appointment]:
    contact = Contact(tenant_id=tenant.id, phone=phone)
    db.add(contact)
    db.flush()

    appointment = appointments_service.create_appointment(
        db,
        tenant,
        contact,
        therapist_name="María Fernanda Ruiz",
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=20),
    )
    appointments_service.mark_reminder_sent(appointment)
    db.commit()
    return contact, appointment


def test_cancel_reply_to_reminder_forces_human_handoff() -> None:
    db = SessionLocal()
    tenant = _grip()
    phone = _test_phone()
    contact, appointment = _seed_pending_reminder(db, tenant, phone)

    try:
        result = process_inbound_message(
            db, tenant, phone, "hola, no voy a poder ir a la cita mañana, tengo que cancelar",
            gemini_client=_ExplodingGeminiClient(),
        )
        db.commit()

        assert result.status == ConversationStatus.WAITING_FOR_HUMAN
        assert result.awaiting_human is True
        assert "equipo" in result.reply_text.lower()

        db.refresh(appointment)
        assert appointment.reminder_response == ReminderResponse.CANCEL_OR_RESCHEDULE
        assert appointment.status == AppointmentStatus.RESCHEDULE_REQUESTED
    finally:
        _cleanup(db, contact.id)
        db.close()


def test_reschedule_reply_to_reminder_forces_human_handoff_even_if_polite() -> None:
    db = SessionLocal()
    tenant = _grip()
    phone = _test_phone()
    contact, appointment = _seed_pending_reminder(db, tenant, phone)

    try:
        result = process_inbound_message(
            db, tenant, phone, "¿podríamos reagendar para otro día? Gracias",
            gemini_client=_ExplodingGeminiClient(),
        )
        db.commit()

        assert result.status == ConversationStatus.WAITING_FOR_HUMAN
        db.refresh(appointment)
        assert appointment.status == AppointmentStatus.RESCHEDULE_REQUESTED
    finally:
        _cleanup(db, contact.id)
        db.close()


def test_confirm_reply_to_reminder_does_not_force_handoff() -> None:
    db = SessionLocal()
    tenant = _grip()
    phone = _test_phone()
    contact, appointment = _seed_pending_reminder(db, tenant, phone)

    try:
        result = process_inbound_message(
            db, tenant, phone, "Sí, ahí estaré",
            gemini_client=_ExplodingGeminiClient(),
        )
        db.commit()

        assert result.status == ConversationStatus.BOT_ACTIVE
        assert result.awaiting_human is False
        assert "gracias por confirmar" in result.reply_text.lower()

        db.refresh(appointment)
        assert appointment.reminder_response == ReminderResponse.CONFIRMED
        assert appointment.status == AppointmentStatus.CONFIRMED
    finally:
        _cleanup(db, contact.id)
        db.close()


def test_unrelated_reply_does_not_touch_the_pending_appointment() -> None:
    """A message that has nothing to do with the reminder (and isn't a
    clean confirm) should fall through to normal handling — the guard
    only ever acts on a positive cancel/reschedule (or confirm) signal."""
    db = SessionLocal()
    tenant = _grip()
    phone = _test_phone()
    from app.services.gemini_client import GeminiAnswer

    class _FakeGemini:
        def answer(self, **kwargs):
            return GeminiAnswer(text="Estamos en Santo Domingo.", tools_used=["get_location"], tool_results={}, usage={})

    contact, appointment = _seed_pending_reminder(db, tenant, phone)

    try:
        result = process_inbound_message(
            db, tenant, phone, "cuentenme sobre GRIP", gemini_client=_FakeGemini(),
        )
        db.commit()

        assert result.status == ConversationStatus.BOT_ACTIVE
        db.refresh(appointment)
        assert appointment.reminder_response is None
        assert appointment.status == AppointmentStatus.SCHEDULED
    finally:
        _cleanup(db, contact.id)
        db.close()

def test_reminders_due_picks_48h_then_24h_and_never_repeats() -> None:
    db = SessionLocal()
    tenant = _grip()
    contact = Contact(tenant_id=tenant.id, phone=_test_phone())
    db.add(contact)
    db.flush()
    now = datetime.now(timezone.utc)
    far = appointments_service.create_appointment(db, tenant, contact, therapist_name="A", scheduled_at=now + timedelta(hours=40))
    near = appointments_service.create_appointment(db, tenant, contact, therapist_name="B", scheduled_at=now + timedelta(hours=10))
    too_soon = appointments_service.create_appointment(db, tenant, contact, therapist_name="C", scheduled_at=now + timedelta(hours=1))
    later = appointments_service.create_appointment(db, tenant, contact, therapist_name="D", scheduled_at=now + timedelta(hours=100))
    db.commit()

    try:
        due = {a.id: kind for a, kind in appointments_service.reminders_due(db, tenant, reference=now)}
        assert due.get(far.id) == "48h"
        assert due.get(near.id) == "24h"
        assert too_soon.id not in due and later.id not in due

        far.reminder_48h_sent_at = now
        db.commit()
        due = {a.id: kind for a, kind in appointments_service.reminders_due(db, tenant, reference=now)}
        assert far.id not in due  # 48h already sent; 24h only once it is within 24h

        due = {a.id: kind for a, kind in appointments_service.reminders_due(db, tenant, reference=now + timedelta(hours=20))}
        assert due.get(far.id) == "24h"
    finally:
        _cleanup(db, contact.id)
        db.close()


def test_send_reminder_marks_the_right_flag_and_includes_notes_summary() -> None:
    db = SessionLocal()
    tenant = _grip()
    contact = Contact(tenant_id=tenant.id, phone=_test_phone(), name="Ana")
    db.add(contact)
    db.flush()
    appt = appointments_service.create_appointment(
        db, tenant, contact, therapist_name="María", scheduled_at=datetime.now(timezone.utc) + timedelta(hours=30)
    )
    try:
        body = appointments_service.send_reminder(db, tenant, appt, contact, kind="48h", summary="la atención")
        db.commit()
        assert "la atención" in body and "PAGAR" in body
        assert appt.reminder_48h_sent_at is not None and appt.reminder_24h_sent_at is None
        assert appt.reminder_sent_at is not None
    finally:
        _cleanup(db, contact.id)
        db.close()


def test_reply_pagar_gets_bank_details_and_stays_pending() -> None:
    db = SessionLocal()
    tenant = _grip()
    phone = _test_phone()
    contact, appointment = _seed_pending_reminder(db, tenant, phone)
    try:
        result = process_inbound_message(db, tenant, phone, "PAGAR", gemini_client=_ExplodingGeminiClient())
        db.commit()
        assert "número de cuenta" in result.reply_text.lower()
        assert result.awaiting_human is False
        db.refresh(appointment)
        assert appointment.reminder_response is None  # still waiting for Sí / cancel
    finally:
        _cleanup(db, contact.id)
        db.close()


def test_reply_ya_pague_goes_to_a_human() -> None:
    db = SessionLocal()
    tenant = _grip()
    phone = _test_phone()
    contact, appointment = _seed_pending_reminder(db, tenant, phone)
    try:
        result = process_inbound_message(db, tenant, phone, "ya pagué, gracias", gemini_client=_ExplodingGeminiClient())
        db.commit()
        assert result.status == ConversationStatus.WAITING_FOR_HUMAN
        assert "verificar" in result.reply_text.lower()
    finally:
        _cleanup(db, contact.id)
        db.close()