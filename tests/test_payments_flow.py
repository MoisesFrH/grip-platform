"""
Payments and agenda against the database: save/verify, reported-by-patient,
conflicts, price list seeding and the bot's "ya pagué" link. Run with:

    .venv/bin/python -m pytest tests/test_payments_flow.py
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.db import SessionLocal
from app.models.appointment import Appointment
from app.models.billing import Payment, PriceItem
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.tenant import Tenant
from app.services import agenda, appointments as ap, payments
from app.services.orchestrator import process_inbound_message


def _grip(db) -> Tenant:
    return db.query(Tenant).filter(Tenant.slug == "grip").one()


def _contact(db, tenant) -> Contact:
    c = Contact(tenant_id=tenant.id, phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}", name="Ana")
    db.add(c)
    db.flush()
    return c


def _cleanup(db, contact_ids) -> None:
    for cid in contact_ids:
        db.query(Payment).filter(Payment.appointment_id.in_(db.query(Appointment.id).filter(Appointment.contact_id == cid))).delete(synchronize_session=False)
        db.query(Appointment).filter(Appointment.contact_id == cid).delete(synchronize_session=False)
        db.query(Message).filter(Message.conversation_id.in_(db.query(Conversation.id).filter(Conversation.contact_id == cid))).delete(synchronize_session=False)
        db.query(Conversation).filter(Conversation.contact_id == cid).delete(synchronize_session=False)
        db.query(Contact).filter(Contact.id == cid).delete(synchronize_session=False)
    db.commit()


class _Boom:
    def answer(self, **kw):
        raise AssertionError("no Gemini")

    def classify_intent(self, **kw):
        raise AssertionError("no Gemini")


def test_save_verify_and_reject_record_who_and_when() -> None:
    db = SessionLocal()
    tenant = _grip(db)
    c = _contact(db, tenant)
    appt = ap.create_appointment(db, tenant, c, therapist_name="Ana", scheduled_at=datetime.now(timezone.utc) + timedelta(days=2))
    try:
        p = payments.save_payment(db, tenant, appt, amount="1500", method="TRANSFER", status="PENDING", reference=None, note=None, actor="Sec")
        assert p.status == "PENDING" and p.verified_by is None
        p = payments.save_payment(db, tenant, appt, amount="1500", method="TRANSFER", status="VERIFIED", reference=" 8841 ", note=None, actor="Sec")
        assert p.verified_by == "Sec" and p.verified_at is not None and p.reference == "8841"
        p = payments.save_payment(db, tenant, appt, amount="1500", method="TRANSFER", status="PENDING", reference=None, note=None, actor="Sec")
        assert p.verified_by is None and p.verified_at is None  # un-verifying clears the audit fields
        with pytest.raises(payments.PaymentError):
            payments.save_payment(db, tenant, appt, amount="1", method="BITCOIN", status="PENDING", reference=None, note=None, actor="Sec")
        assert db.query(Payment).filter(Payment.appointment_id == appt.id).count() == 1  # one row per appointment
    finally:
        db.rollback()
        _cleanup(db, [c.id])
        db.close()


def test_mark_reported_never_downgrades_a_verified_payment() -> None:
    db = SessionLocal()
    tenant = _grip(db)
    c = _contact(db, tenant)
    appt = ap.create_appointment(db, tenant, c, therapist_name="Ana", scheduled_at=datetime.now(timezone.utc) + timedelta(days=2))
    try:
        assert payments.mark_reported(db, tenant, appt).status == "REPORTED"
        payments.save_payment(db, tenant, appt, amount="900", method="TRANSFER", status="VERIFIED", reference=None, note=None, actor="Sec")
        assert payments.mark_reported(db, tenant, appt).status == "VERIFIED"
    finally:
        db.rollback()
        _cleanup(db, [c.id])
        db.close()


def test_bot_ya_pague_marks_the_payment_as_reported() -> None:
    db = SessionLocal()
    tenant = _grip(db)
    c = _contact(db, tenant)
    appt = ap.create_appointment(db, tenant, c, therapist_name="Ana", scheduled_at=datetime.now(timezone.utc) + timedelta(hours=20))
    ap.mark_reminder_sent(appt)
    db.commit()
    try:
        process_inbound_message(db, tenant, c.phone, "ya pagué, gracias", gemini_client=_Boom())
        db.commit()
        pay = db.query(Payment).filter(Payment.appointment_id == appt.id).one()
        assert pay.status == "REPORTED" and pay.reported_at is not None
    finally:
        _cleanup(db, [c.id])
        db.close()


def test_find_conflicts_same_therapist_same_patient_and_cancelled_frees_slot() -> None:
    db = SessionLocal()
    tenant = _grip(db)
    c1, c2 = _contact(db, tenant), _contact(db, tenant)
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(days=3)
    a1 = ap.create_appointment(db, tenant, c1, therapist_name="Ana", scheduled_at=start)
    db.commit()
    try:
        # same therapist, other patient, overlapping by 30 min
        found = agenda.find_conflicts(db, tenant, therapist_name="ana", contact_id=c2.id, start=start + timedelta(minutes=30))
        assert [kind for _, kind in found] == ["therapist"]
        # same patient with another therapist
        found = agenda.find_conflicts(db, tenant, therapist_name="Beto", contact_id=c1.id, start=start)
        assert [kind for _, kind in found] == ["patient"]
        # back to back is fine; excluding itself (reschedule) is fine
        assert not agenda.find_conflicts(db, tenant, therapist_name="Ana", contact_id=c2.id, start=start + timedelta(hours=1))
        assert not agenda.find_conflicts(db, tenant, therapist_name="Ana", contact_id=c1.id, start=start, exclude_id=a1.id)
        # cancelled appointments free the slot
        from app.models.appointment import AppointmentStatus
        a1.status = AppointmentStatus.CANCELLED
        db.commit()
        assert not agenda.find_conflicts(db, tenant, therapist_name="Ana", contact_id=c2.id, start=start)
    finally:
        db.rollback()
        _cleanup(db, [c1.id, c2.id])
        db.close()


def test_price_items_are_created_blank_from_services_and_keep_filled_amounts() -> None:
    db = SessionLocal()
    tenant = _grip(db)
    try:
        payments.ensure_price_items(db, tenant)
        db.commit()
        item = db.query(PriceItem).filter(PriceItem.tenant_id == tenant.id).first()
        assert item is not None
        original = item.amount
        item.amount = 1234
        db.commit()
        payments.ensure_price_items(db, tenant)  # re-running (every deploy) must not reset amounts
        db.commit()
        db.refresh(item)
        assert float(item.amount) == 1234.0
        item.amount = original
        db.commit()
    finally:
        db.close()