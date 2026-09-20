"""
Local, no-cost simulator (dev-only): lets a patient chat and an agent work
the human inbox entirely inside this app, with no Twilio account and no
WhatsApp number involved. Wraps the same orchestrator + human_inbox modules
a real Twilio webhook would call — this is a thin adapter, not a parallel
implementation, so what works here is exactly what will work once Twilio is
wired in.

Not for production: no auth, single hardcoded tenant by default, and the
"patient" side is simulated by whatever phone string the browser makes up
for itself (see the UI at GET /simulator).
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.models.contact import Contact
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message, MessageSender
from app.models.tenant import Tenant
from app.services import appointments as appointments_service
from app.services import crm, human_inbox
from app.services.gemini_client import GeminiClient
from app.services.orchestrator import process_inbound_message

router = APIRouter(prefix="/simulator", tags=["simulator"])

# One process-wide client is enough here, but google-genai's Client()
# validates the API key eagerly at construction time — so without a
# GEMINI_API_KEY set (e.g. running the simulator purely to demo the state
# machine / human handoff, before any Gemini access is set up) we simply
# don't build one. orchestrator.process_inbound_message already treats
# gemini_client=None as "answer this category-A intent by handing off to a
# human" (fail closed), so category-A messages still get a sane reply —
# they just won't be Gemini-grounded until a real key is configured.
_gemini_client: GeminiClient | None
try:
    _gemini_client = GeminiClient() if get_settings().gemini_api_key else None
except Exception:
    _gemini_client = None


def _get_tenant(db: Session, tenant_slug: str) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.slug == tenant_slug).one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"Unknown tenant slug: {tenant_slug}")
    return tenant


def _get_conversation(db: Session, conversation_id: str) -> Conversation:
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail=f"Unknown conversation: {conversation_id}")
    return conversation


# --- schemas -----------------------------------------------------------


class SimulatorMessageRequest(BaseModel):
    tenant_slug: str = "grip"
    phone: str
    body: str
    whatsapp_profile_name: str | None = None


class SimulatorMessageResponse(BaseModel):
    conversation_id: str
    status: str
    reply_text: str | None
    intent: str | None
    awaiting_human: bool


class MessageOut(BaseModel):
    id: str
    direction: str
    sender: str
    body: str | None
    intent: str | None
    created_at: str


class ConversationOut(BaseModel):
    id: str
    status: str
    contact_phone: str
    contact_name: str | None
    assigned_to: str | None
    handoff_reason: str | None
    handoff_priority: str | None
    last_message_at: str | None
    last_message_preview: str | None


class TestReminderRequest(BaseModel):
    tenant_slug: str = "grip"
    phone: str
    whatsapp_profile_name: str | None = None
    therapist_name: str = "María Fernanda Ruiz"
    hours_until_appointment: float = 20.0  # inside the "tomorrow" window appointments.appointments_needing_reminder uses


class TestReminderResponse(BaseModel):
    conversation_id: str
    appointment_id: str
    scheduled_at: str
    reminder_text: str


class ClaimRequest(BaseModel):
    agent_name: str


class AgentReplyRequest(BaseModel):
    body: str


# --- patient side --------------------------------------------------------


@router.post("/message", response_model=SimulatorMessageResponse)
def send_patient_message(payload: SimulatorMessageRequest, db: Session = Depends(get_db)) -> SimulatorMessageResponse:
    """The patient side of the simulator: one chat bubble in, the bot's (or
    silence, if a human owns the conversation) reply out. Exactly what a
    Twilio webhook handler will do once one exists."""
    tenant = _get_tenant(db, payload.tenant_slug)

    result = process_inbound_message(
        db,
        tenant,
        payload.phone,
        payload.body,
        whatsapp_profile_name=payload.whatsapp_profile_name,
        gemini_client=_gemini_client,
    )
    db.commit()

    return SimulatorMessageResponse(
        conversation_id=result.conversation_id,
        status=result.status.value,
        reply_text=result.reply_text,
        intent=result.classification.primary_intent.value if result.classification else None,
        awaiting_human=result.awaiting_human,
    )


@router.post("/appointments/test-reminder", response_model=TestReminderResponse)
def send_test_reminder(payload: TestReminderRequest, db: Session = Depends(get_db)) -> TestReminderResponse:
    """"Probar recordatorio" button: creates a throwaway appointment for
    this contact roughly a day out, then immediately sends the reminder
    through the exact same app.services.appointments.send_reminder
    function the real daily cron job (scripts/send_appointment_reminders)
    calls — so this is a live test of the actual reminder pipeline
    (including the forced-handoff guard if you reply with something like
    "necesito cancelar"), not a mocked-up preview of it."""
    tenant = _get_tenant(db, payload.tenant_slug)
    contact = crm.get_or_create_contact(
        db, tenant, payload.phone, whatsapp_profile_name=payload.whatsapp_profile_name
    )
    scheduled_at = datetime.now(timezone.utc) + timedelta(hours=payload.hours_until_appointment)
    appointment = appointments_service.create_appointment(
        db, tenant, contact, therapist_name=payload.therapist_name, scheduled_at=scheduled_at
    )
    reminder_text = appointments_service.send_reminder(db, tenant, appointment, contact)
    db.commit()

    conversation = (
        db.query(Conversation)
        .filter(Conversation.contact_id == contact.id)
        .order_by(Conversation.started_at.desc())
        .first()
    )
    return TestReminderResponse(
        conversation_id=str(conversation.id),
        appointment_id=str(appointment.id),
        scheduled_at=appointment.scheduled_at.isoformat(),
        reminder_text=reminder_text,
    )


@router.get("/conversations/by-phone/{tenant_slug}/{phone}/messages", response_model=list[MessageOut])
def get_conversation_messages_by_phone(tenant_slug: str, phone: str, db: Session = Depends(get_db)) -> list[MessageOut]:
    """What the patient-side chat pane polls: the full transcript for
    whichever contact this browser tab is pretending to be, across their
    current open conversation (or their most recent one, if none is open)."""
    tenant = _get_tenant(db, tenant_slug)
    contact = db.query(Contact).filter(Contact.tenant_id == tenant.id, Contact.phone == phone).one_or_none()
    if contact is None:
        return []

    conversation = (
        db.query(Conversation)
        .filter(Conversation.contact_id == contact.id)
        .order_by(Conversation.started_at.desc())
        .first()
    )
    if conversation is None:
        return []

    return [_message_out(m) for m in conversation.messages]


# --- agent inbox side ------------------------------------------------------


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(tenant_slug: str = "grip", db: Session = Depends(get_db)) -> list[ConversationOut]:
    """Everything the agent inbox pane shows: conversations waiting for a
    human plus whatever's currently claimed, oldest-first (same ordering
    human_inbox itself uses for prioritization)."""
    tenant = _get_tenant(db, tenant_slug)
    waiting = human_inbox.list_waiting_conversations(db, tenant)
    active = human_inbox.list_active_conversations(db, tenant)
    return [_conversation_out(c) for c in [*waiting, *active]]


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def get_conversation_messages(conversation_id: str, db: Session = Depends(get_db)) -> list[MessageOut]:
    conversation = _get_conversation(db, conversation_id)
    return [_message_out(m) for m in conversation.messages]


@router.post("/conversations/{conversation_id}/claim", response_model=ConversationOut)
def claim_conversation(conversation_id: str, payload: ClaimRequest, db: Session = Depends(get_db)) -> ConversationOut:
    conversation = _get_conversation(db, conversation_id)
    try:
        human_inbox.claim_conversation(db, conversation, agent_name=payload.agent_name)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return _conversation_out(conversation)


@router.post("/conversations/{conversation_id}/reply", response_model=MessageOut)
def agent_reply(conversation_id: str, payload: AgentReplyRequest, db: Session = Depends(get_db)) -> MessageOut:
    conversation = _get_conversation(db, conversation_id)
    if conversation.status != ConversationStatus.HUMAN_ACTIVE:
        raise HTTPException(status_code=400, detail="Conversation must be claimed (HUMAN_ACTIVE) before an agent can reply.")
    message = human_inbox.agent_reply(db, conversation, payload.body)
    db.commit()
    return _message_out(message)


@router.post("/conversations/{conversation_id}/resolve", response_model=ConversationOut)
def resolve_conversation(conversation_id: str, db: Session = Depends(get_db)) -> ConversationOut:
    conversation = _get_conversation(db, conversation_id)
    try:
        human_inbox.resolve_conversation(db, conversation)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return _conversation_out(conversation)


@router.post("/conversations/{conversation_id}/return-to-bot", response_model=ConversationOut)
def return_to_bot(conversation_id: str, db: Session = Depends(get_db)) -> ConversationOut:
    conversation = _get_conversation(db, conversation_id)
    try:
        human_inbox.return_to_bot(db, conversation)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return _conversation_out(conversation)


# --- serialization helpers ------------------------------------------------


def _message_out(message: Message) -> MessageOut:
    return MessageOut(
        id=str(message.id),
        direction=message.direction.value,
        sender=message.sender.value,
        body=message.body,
        intent=message.intent,
        created_at=message.created_at.isoformat(),
    )


def _conversation_out(conversation: Conversation) -> ConversationOut:
    last_message = conversation.messages[-1] if conversation.messages else None
    return ConversationOut(
        id=str(conversation.id),
        status=conversation.status.value,
        contact_phone=conversation.contact.phone,
        contact_name=conversation.contact.whatsapp_profile_name or conversation.contact.name,
        assigned_to=conversation.assigned_to,
        handoff_reason=conversation.handoff_reason,
        handoff_priority=conversation.handoff_priority,
        last_message_at=conversation.last_message_at.isoformat() if conversation.last_message_at else None,
        last_message_preview=(last_message.body[:120] if last_message and last_message.body else None),
    )