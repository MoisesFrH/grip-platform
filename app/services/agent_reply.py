"""
Staff replying to a patient from the CRM (Casos escalados tab): take the case,
send WhatsApp messages as a human agent, then hand the conversation back to
the bot or close it.

Every state change goes through conversation_state.apply_transition (actor
HUMAN_AGENT), so the rules in that module still decide what is allowed. While
the case is HUMAN_ACTIVE the bot stays silent.

WhatsApp only lets a business send free-form text within 24 hours of the
patient's last message. Outside that window the send is refused here
(OutsideWindowError) instead of failing silently at Meta.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.contact import Contact
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message, MessageDirection, MessageSender
from app.services import crm, meta_whatsapp
from app.services.conversation_state import Actor, ConversationEvent, apply_transition
from app.services.messaging import send_whatsapp_message

logger = logging.getLogger(__name__)

WINDOW_HOURS = 24
_OPEN_FOR_REPLY = (ConversationStatus.WAITING_FOR_HUMAN, ConversationStatus.HUMAN_ACTIVE)


class AgentReplyError(Exception):
    """Base class; the message is safe to show to staff (Spanish)."""


class OutsideWindowError(AgentReplyError):
    pass


class DeliveryFailedError(AgentReplyError):
    pass


class InvalidStateError(AgentReplyError):
    pass


def last_patient_message_at(conversation: Conversation) -> datetime | None:
    times = [m.created_at for m in conversation.messages if m.direction == MessageDirection.INBOUND]
    return max(times) if times else None


def window_open(conversation: Conversation, *, now: datetime | None = None) -> bool:
    last = last_patient_message_at(conversation)
    if last is None:
        return False
    return (now or datetime.now(timezone.utc)) - last <= timedelta(hours=WINDOW_HOURS)


def _deliver(contact: Contact, text: str) -> bool:
    settings = get_settings()
    if settings.meta_access_token and settings.meta_phone_number_id:
        return meta_whatsapp.send_text(settings.meta_access_token, settings.meta_phone_number_id, contact.phone, text)
    send_whatsapp_message(contact.phone, text)  # logging stub (development)
    return True


def claim(conversation: Conversation, agent_name: str) -> None:
    """WAITING_FOR_HUMAN -> HUMAN_ACTIVE. Already HUMAN_ACTIVE: just records who."""
    if conversation.status == ConversationStatus.WAITING_FOR_HUMAN:
        apply_transition(conversation, ConversationEvent.AGENT_CLAIMED, Actor.HUMAN_AGENT)
    elif conversation.status != ConversationStatus.HUMAN_ACTIVE:
        raise InvalidStateError("Este caso no está esperando a una persona.")
    conversation.assigned_to = agent_name
    if conversation.claimed_at is None:
        conversation.claimed_at = datetime.now(timezone.utc)


def send_reply(db: Session, conversation: Conversation, agent_name: str, text: str) -> Message:
    text = text.strip()
    if not text:
        raise AgentReplyError("Escribe un mensaje.")
    if conversation.status not in _OPEN_FOR_REPLY:
        raise InvalidStateError("Solo puedes responder casos que esperan a una persona o que ya tomaste.")
    if not window_open(conversation):
        raise OutsideWindowError(
            "Pasaron más de 24 horas desde el último mensaje del paciente, y WhatsApp no permite escribirle "
            "texto libre. Hace falta una plantilla aprobada de Meta."
        )
    contact = conversation.contact
    if not _deliver(contact, text):
        raise DeliveryFailedError("No se pudo enviar el mensaje por WhatsApp. Intenta de nuevo.")
    claim(conversation, agent_name)  # replying takes the case if nobody had
    return crm.record_outbound_message(db, conversation, text, sender=MessageSender.HUMAN_AGENT)


def return_to_bot(conversation: Conversation, agent_name: str) -> None:
    """Hands the conversation back to the bot (the 'return to AI' button)."""
    if conversation.status == ConversationStatus.WAITING_FOR_HUMAN:
        claim(conversation, agent_name)
    if conversation.status == ConversationStatus.HUMAN_ACTIVE:
        apply_transition(conversation, ConversationEvent.AGENT_RESOLVED, Actor.HUMAN_AGENT)
    if conversation.status == ConversationStatus.HUMAN_RESOLVED:
        apply_transition(conversation, ConversationEvent.BOT_REACTIVATED, Actor.HUMAN_AGENT)
    else:
        raise InvalidStateError("Este caso ya no está en manos de una persona.")


def close(conversation: Conversation) -> None:
    if conversation.status == ConversationStatus.CLOSED:
        raise InvalidStateError("Este caso ya está cerrado.")
    apply_transition(conversation, ConversationEvent.CONVERSATION_CLOSED, Actor.HUMAN_AGENT)