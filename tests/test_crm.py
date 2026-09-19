"""
CRM service layer tests against the real Postgres database (no mocking —
these are the actual queries that will run in production). Creates and
cleans up its own contact/conversation/message rows under the seeded GRIP
tenant so it's safe to re-run.

Run with:
    .venv/bin/python -m tests.test_crm
"""

import uuid

from app.core.db import SessionLocal
from app.models.contact import Contact
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message
from app.models.tenant import Tenant
from app.services.crm import (
    get_or_create_contact,
    get_or_create_open_conversation,
    normalize_whatsapp_address,
    record_inbound_message,
)


def _test_phone() -> str:
    # A fresh, obviously-fake number per test run so tests never collide
    # with each other or with real seeded data.
    return f"+1555{uuid.uuid4().int % 10_000_000:07d}"


def _cleanup(db, contact_id) -> None:
    db.query(Message).filter(Message.conversation_id.in_(
        db.query(Conversation.id).filter(Conversation.contact_id == contact_id)
    )).delete(synchronize_session=False)
    db.query(Conversation).filter(Conversation.contact_id == contact_id).delete(synchronize_session=False)
    db.query(Contact).filter(Contact.id == contact_id).delete(synchronize_session=False)
    db.commit()


def test_normalize_strips_whatsapp_prefix() -> None:
    assert normalize_whatsapp_address("whatsapp:+593999999999") == "+593999999999"
    assert normalize_whatsapp_address("+593999999999") == "+593999999999"
    print("PASS: test_normalize_strips_whatsapp_prefix")


def test_get_or_create_contact_is_idempotent() -> None:
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == "grip").one()
    phone = _test_phone()

    try:
        first = get_or_create_contact(db, tenant, f"whatsapp:{phone}", whatsapp_profile_name="Juan Pérez")
        db.commit()
        first_id = first.id

        second = get_or_create_contact(db, tenant, phone)  # same phone, no "whatsapp:" prefix this time
        db.commit()

        assert second.id == first_id
        assert second.whatsapp_profile_name == "Juan Pérez"  # not overwritten by the second call's None

        count = db.query(Contact).filter(Contact.tenant_id == tenant.id, Contact.phone == phone).count()
        assert count == 1

        _cleanup(db, first_id)
        print("PASS: test_get_or_create_contact_is_idempotent")
    finally:
        db.close()


def test_open_conversation_is_reused_until_closed() -> None:
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == "grip").one()
    phone = _test_phone()

    try:
        contact = get_or_create_contact(db, tenant, phone)
        db.commit()

        conv1 = get_or_create_open_conversation(db, tenant, contact)
        db.commit()
        conv1_id = conv1.id
        assert conv1.status == ConversationStatus.BOT_ACTIVE

        # A second inbound message on the same open conversation must reuse it.
        conv2 = get_or_create_open_conversation(db, tenant, contact)
        db.commit()
        assert conv2.id == conv1_id

        # Close it, then simulate "the patient returns days later" —
        # architecture doc §11: this MUST be a new conversation, not a reopen.
        conv2.status = ConversationStatus.CLOSED
        db.commit()

        conv3 = get_or_create_open_conversation(db, tenant, contact)
        db.commit()
        assert conv3.id != conv1_id
        assert conv3.status == ConversationStatus.BOT_ACTIVE

        _cleanup(db, contact.id)
        print("PASS: test_open_conversation_is_reused_until_closed")
    finally:
        db.close()


def test_inbound_message_is_deduplicated_by_twilio_id() -> None:
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == "grip").one()
    phone = _test_phone()

    try:
        contact = get_or_create_contact(db, tenant, phone)
        conversation = get_or_create_open_conversation(db, tenant, contact)
        db.commit()

        twilio_sid = f"SM{uuid.uuid4().hex}"

        first = record_inbound_message(db, conversation, "hola", twilio_message_id=twilio_sid)
        db.commit()

        # Simulate Twilio retrying the same webhook delivery.
        second = record_inbound_message(db, conversation, "hola", twilio_message_id=twilio_sid)
        db.commit()

        assert first.id == second.id

        count = db.query(Message).filter(Message.twilio_message_id == twilio_sid).count()
        assert count == 1

        _cleanup(db, contact.id)
        print("PASS: test_inbound_message_is_deduplicated_by_twilio_id")
    finally:
        db.close()


if __name__ == "__main__":
    test_normalize_strips_whatsapp_prefix()
    test_get_or_create_contact_is_idempotent()
    test_open_conversation_is_reused_until_closed()
    test_inbound_message_is_deduplicated_by_twilio_id()
    print("\nAll CRM service layer tests passed.")
