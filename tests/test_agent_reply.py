"""
Staff replying from the CRM: claim, reply (24h window), hand back to the bot,
close. Needs the database. Run with:

    .venv/bin/python -m pytest tests/test_agent_reply.py
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.db import SessionLocal
from app.models.contact import Contact
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message, MessageDirection, MessageSender, MessageStatus
from app.models.tenant import Tenant
from app.services import agent_reply


def _setup(db, *, hours_since_patient_msg: float):
    tenant = db.query(Tenant).filter(Tenant.slug == "grip").one()
    contact = Contact(tenant_id=tenant.id, phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}")
    db.add(contact)
    db.flush()
    now = datetime.now(timezone.utc)
    conv = Conversation(
        tenant_id=tenant.id, contact_id=contact.id, status=ConversationStatus.WAITING_FOR_HUMAN, started_at=now
    )
    db.add(conv)
    db.flush()
    db.add(Message(
        tenant_id=tenant.id, conversation_id=conv.id, direction=MessageDirection.INBOUND,
        sender=MessageSender.PATIENT, body="ya pagué", status=MessageStatus.RECEIVED,
        created_at=now - timedelta(hours=hours_since_patient_msg),
    ))
    db.commit()
    db.refresh(conv)
    return conv, contact


def _cleanup(db, conv, contact):
    db.query(Message).filter(Message.conversation_id == conv.id).delete(synchronize_session=False)
    db.query(Conversation).filter(Conversation.id == conv.id).delete(synchronize_session=False)
    db.query(Contact).filter(Contact.id == contact.id).delete(synchronize_session=False)
    db.commit()


def test_reply_claims_sends_and_records(monkeypatch) -> None:
    sent = []
    monkeypatch.setattr(agent_reply, "_deliver", lambda contact, text: sent.append(text) or True)
    db = SessionLocal()
    conv, contact = _setup(db, hours_since_patient_msg=1)
    try:
        agent_reply.send_reply(db, conv, "Secretaria Ana", "Tu pago fue verificado, gracias.")
        db.commit()
        db.refresh(conv)
        assert sent == ["Tu pago fue verificado, gracias."]
        assert conv.status == ConversationStatus.HUMAN_ACTIVE
        assert conv.assigned_to == "Secretaria Ana" and conv.claimed_at is not None
        assert conv.messages[-1].sender == MessageSender.HUMAN_AGENT
    finally:
        _cleanup(db, conv, contact)
        db.close()


def test_reply_refused_after_24h(monkeypatch) -> None:
    monkeypatch.setattr(agent_reply, "_deliver", lambda contact, text: pytest.fail("must not send"))
    db = SessionLocal()
    conv, contact = _setup(db, hours_since_patient_msg=30)
    try:
        with pytest.raises(agent_reply.OutsideWindowError):
            agent_reply.send_reply(db, conv, "Ana", "hola")
        assert conv.status == ConversationStatus.WAITING_FOR_HUMAN
    finally:
        _cleanup(db, conv, contact)
        db.close()


def test_failed_delivery_records_nothing(monkeypatch) -> None:
    monkeypatch.setattr(agent_reply, "_deliver", lambda contact, text: False)
    db = SessionLocal()
    conv, contact = _setup(db, hours_since_patient_msg=1)
    try:
        with pytest.raises(agent_reply.DeliveryFailedError):
            agent_reply.send_reply(db, conv, "Ana", "hola")
        db.rollback()
        db.refresh(conv)
        assert len(conv.messages) == 1 and conv.status == ConversationStatus.WAITING_FOR_HUMAN
    finally:
        _cleanup(db, conv, contact)
        db.close()


def test_return_to_bot_and_close() -> None:
    db = SessionLocal()
    conv, contact = _setup(db, hours_since_patient_msg=1)
    try:
        agent_reply.return_to_bot(conv, "Ana")
        assert conv.status == ConversationStatus.BOT_RESUMED
        with pytest.raises(agent_reply.InvalidStateError):
            agent_reply.return_to_bot(conv, "Ana")
        agent_reply.close(conv)
        assert conv.status == ConversationStatus.CLOSED
        with pytest.raises(agent_reply.InvalidStateError):
            agent_reply.close(conv)
    finally:
        db.rollback()
        _cleanup(db, conv, contact)
        db.close()