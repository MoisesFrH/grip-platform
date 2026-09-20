"""
The human-agent side of the hand-off (architecture doc §14 human inbox):
take over, reply, resolve, return to bot. Each action here is a thin
wrapper around a single conversation_state transition plus the CRM write
it implies — this is what the future inbox UI (and, today, the simulator)
calls.

Never commits — the caller (an API route) owns the transaction.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message, MessageSender
from app.models.tenant import Tenant
from app.services import crm
from app.services.conversation_state import Actor, ConversationEvent, apply_transition


def list_waiting_conversations(db: Session, tenant: Tenant) -> list[Conversation]:
    """Conversations sitting in WAITING_FOR_HUMAN, most urgent/oldest
    first — what an agent's inbox landing view shows."""
    return (
        db.query(Conversation)
        .filter(Conversation.tenant_id == tenant.id, Conversation.status == ConversationStatus.WAITING_FOR_HUMAN)
        .order_by(Conversation.last_message_at.asc())
        .all()
    )


def list_active_conversations(db: Session, tenant: Tenant, agent_name: str | None = None) -> list[Conversation]:
    """Conversations an agent is currently handling (HUMAN_ACTIVE),
    optionally filtered to one agent."""
    query = db.query(Conversation).filter(
        Conversation.tenant_id == tenant.id, Conversation.status == ConversationStatus.HUMAN_ACTIVE
    )
    if agent_name:
        query = query.filter(Conversation.assigned_to == agent_name)
    return query.order_by(Conversation.last_message_at.asc()).all()


def claim_conversation(db: Session, conversation: Conversation, agent_name: str) -> Conversation:
    """The 'take over' button (architecture doc §14). Records claimed_at
    (overwritten on each claim, so it always reflects the most recent one)
    — that's what the "time to claim a critical case" dashboard metric is
    computed from, paired with handoff_triggered_at."""
    apply_transition(conversation, ConversationEvent.AGENT_CLAIMED, Actor.HUMAN_AGENT)
    conversation.assigned_to = agent_name
    conversation.claimed_at = datetime.now(timezone.utc)
    db.flush()
    return conversation


def agent_reply(db: Session, conversation: Conversation, body: str) -> Message:
    """An agent's own message to the patient. Requires HUMAN_ACTIVE —
    conversation_state doesn't gate this directly (replying isn't a state
    transition), so the caller must ensure the conversation was claimed
    first; this is enforced at the API route layer."""
    return crm.record_outbound_message(db, conversation, body, sender=MessageSender.HUMAN_AGENT)


def resolve_conversation(db: Session, conversation: Conversation) -> Conversation:
    """The 'mark resolved' action — does NOT hand control back to the bot
    by itself (architecture doc §6: that's a separate, explicit action).
    A patient message after this re-opens WAITING_FOR_HUMAN rather than
    silently reactivating the bot (see conversation_state.py)."""
    apply_transition(conversation, ConversationEvent.AGENT_RESOLVED, Actor.HUMAN_AGENT)
    db.flush()
    return conversation


def return_to_bot(db: Session, conversation: Conversation) -> Conversation:
    """The 'return to AI' button."""
    apply_transition(conversation, ConversationEvent.BOT_REACTIVATED, Actor.HUMAN_AGENT)
    conversation.assigned_to = None
    db.flush()
    return conversation