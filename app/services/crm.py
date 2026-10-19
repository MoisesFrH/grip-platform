"""
CRM service layer (architecture doc §4 steps 3-7, §11 CRM data model).

This is the channel-agnostic core of "a message came in, what conversation
does it belong to, and how do we safely record it" — it knows nothing about
Twilio, WhatsApp webhooks, or HTTP. The future webhook handler is a thin
adapter that extracts (tenant, phone, body, twilio_message_id) from a
Twilio POST and calls into this module. That seam is deliberate: it's what
lets the whole pipeline built so far (safety, intent classifier, Gemini
tools, state machine) be exercised and tested today, before a Twilio
account is wired up at all.
"""

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.contact import Contact
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message, MessageDirection, MessageSender, MessageStatus
from app.models.tenant import Tenant


def normalize_whatsapp_address(raw: str) -> str:
    """Strips Twilio's 'whatsapp:' scheme prefix, leaving the bare phone
    number (or BSUID-format address) used as the CRM's contact key.
    'whatsapp:+593999999999' -> '+593999999999'."""
    return raw.removeprefix("whatsapp:").strip()


def get_or_create_contact(db: Session, tenant: Tenant, phone: str, whatsapp_profile_name: str | None = None) -> Contact:
    """One contact per (tenant_id, phone) — architecture doc §11. Idempotent:
    calling this twice for the same tenant+phone returns the same row."""
    phone = normalize_whatsapp_address(phone)

    contact = (
        db.query(Contact)
        .filter(Contact.tenant_id == tenant.id, Contact.phone == phone)
        .one_or_none()
    )

    now = datetime.now(timezone.utc)

    if contact is None:
        contact = Contact(
            tenant_id=tenant.id,
            phone=phone,
            whatsapp_profile_name=whatsapp_profile_name,
            last_interaction_at=now,
        )
        db.add(contact)
        db.flush()  # assigns contact.id without committing the transaction
    else:
        contact.last_interaction_at = now
        if whatsapp_profile_name and not contact.whatsapp_profile_name:
            contact.whatsapp_profile_name = whatsapp_profile_name

    return contact


def lock_contact_conversation_pipeline(db: Session, contact: Contact) -> None:
    """
    Acquires a Postgres transaction-scoped advisory lock keyed on this
    contact's id, before anything else touches their conversation.

    This exists because SELECT ... FOR UPDATE, on its own, CANNOT prevent
    the race that matters most here: two concurrent requests (a Twilio
    retry, or the patient firing off two messages back to back) both
    finding "no open conversation yet" and both creating one. Under
    Postgres's default READ COMMITTED isolation, a row that another,
    still-open transaction has inserted but not committed is simply
    invisible to a concurrent SELECT — FOR UPDATE has nothing to lock
    onto, so it does not block. An advisory lock has no such gap: it is
    keyed on the contact, not on a row that may not exist yet, so the
    second caller blocks until the first transaction commits or rolls
    back, and only then does its own (now correctly informed) lookup.

    Must be called inside an open transaction — the lock releases
    automatically at commit/rollback (pg_advisory_XACT_lock), so it can
    never be leaked by a crashed process holding a session-level lock
    forever.
    """
    # hashtext() collapses the UUID to a 32-bit int; pg_advisory_xact_lock
    # takes it as a bigint. A hash collision between two different
    # contacts just serializes them unnecessarily for an instant — safe,
    # merely a little more conservative than it strictly needs to be.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": str(contact.id)})


def get_open_conversation_for_update(db: Session, tenant: Tenant, contact: Contact) -> Conversation | None:
    """
    Row-locks (SELECT ... FOR UPDATE) the contact's open conversation, if
    one already exists, so a second transaction that arrives AFTER this
    one has committed sees a consistent row to update rather than a
    half-written one. This alone does not prevent double-creation (see
    lock_contact_conversation_pipeline) — the two are meant to be used
    together, which get_or_create_open_conversation below does.

    Returns None when the contact has no open conversation — including
    when their most recent one is CLOSED, which is exactly the "patient
    returns days later" case (architecture doc §11): that gets a brand
    new conversation, never a reopened one.
    """
    return (
        db.query(Conversation)
        .filter(
            Conversation.tenant_id == tenant.id,
            Conversation.contact_id == contact.id,
            Conversation.status != ConversationStatus.CLOSED,
        )
        .order_by(Conversation.started_at.desc())
        .with_for_update()
        .first()
    )


def get_or_create_open_conversation(db: Session, tenant: Tenant, contact: Contact) -> Conversation:
    """
    The single entry point the router calls. Acquires the per-contact
    advisory lock FIRST — that's what makes the rest of this function
    safe to call concurrently for the same contact from two different
    requests/threads/processes.
    """
    lock_contact_conversation_pipeline(db, contact)

    conversation = get_open_conversation_for_update(db, tenant, contact)
    if conversation is not None:
        return conversation

    now = datetime.now(timezone.utc)
    conversation = Conversation(
        tenant_id=tenant.id,
        contact_id=contact.id,
        status=ConversationStatus.BOT_ACTIVE,
        started_at=now,
        last_message_at=now,
    )
    db.add(conversation)
    db.flush()
    return conversation


def record_inbound_message(
    db: Session,
    conversation: Conversation,
    body: str,
    twilio_message_id: str | None = None,
    intent: str | None = None,
) -> Message:
    """
    Idempotent on twilio_message_id (architecture doc §4 step 2): if a
    message with that Twilio SID was already recorded — a webhook retry
    after a slow response, most commonly — this returns the EXISTING row
    instead of inserting a duplicate.
    """
    if twilio_message_id:
        existing = db.query(Message).filter(Message.twilio_message_id == twilio_message_id).one_or_none()
        if existing is not None:
            return existing

    message = Message(
        tenant_id=conversation.tenant_id,
        # Set via the relationship (not just conversation_id) so
        # conversation.messages reflects this new row immediately in
        # memory — callers like the orchestrator read conversation.messages
        # right after recording, and setting only the raw FK would leave
        # that in-memory collection stale until the session re-queries it.
        conversation=conversation,
        direction=MessageDirection.INBOUND,
        sender=MessageSender.PATIENT,
        body=body,
        intent=intent,
        twilio_message_id=twilio_message_id,
        status=MessageStatus.RECEIVED,
    )
    db.add(message)
    conversation.last_message_at = datetime.now(timezone.utc)
    db.flush()
    return message


def record_outbound_message(
    db: Session,
    conversation: Conversation,
    body: str,
    sender: MessageSender,
    twilio_message_id: str | None = None,
) -> Message:
    """sender distinguishes a bot-generated reply from a human agent's own
    message — both are 'outbound' but the router/human-inbox need to tell
    them apart (architecture doc §18 audit trail)."""
    message = Message(
        tenant_id=conversation.tenant_id,
        conversation=conversation,
        direction=MessageDirection.OUTBOUND,
        sender=sender,
        body=body,
        twilio_message_id=twilio_message_id,
        status=MessageStatus.QUEUED,
    )
    db.add(message)
    conversation.last_message_at = datetime.now(timezone.utc)
    db.flush()
    return message