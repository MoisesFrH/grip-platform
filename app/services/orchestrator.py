"""
The channel-agnostic pipeline: one inbound message in, one outcome out.

    contact -> conversation -> safety+intent -> route -> reply / handoff

This is deliberately the ONLY module that wires together crm,
conversation_state, intent_classifier, safety_protocol and gemini_client —
every other module stays focused on its own piece. A future Twilio webhook
handler (or, today, the /simulator routes) is a thin adapter: extract
(tenant, phone, body) from wherever the message came from, call
`process_inbound_message`, send `reply_text` back through that same
channel if it isn't None.

Does not commit the DB session — the caller owns the transaction boundary,
same convention as app.services.crm.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.conversation import Conversation, ConversationStatus
from app.models.message import MessageSender
from app.models.tenant import Tenant
from app.services import crm
from app.services.conversation_state import Actor, ConversationEvent, apply_transition
from app.services.intent_classifier import IntentClassificationResult, classify_intent
from app.services.intents import IntentCategory
from app.services.safety_protocol import resolve_protocol_action

GENERIC_ADMIN_HANDOFF_MESSAGE = (
    "Voy a conectarte con alguien de nuestro equipo para ayudarte con esto. "
    "En un momento te responden por aquí mismo."
)
GENERIC_UNCLEAR_RETRY_MESSAGE = (
    "Disculpa, no estoy segura de haber entendido bien. "
    "¿Puedes contarme un poco más sobre lo que necesitas?"
)
REOPENED_AFTER_RESOLVED_MESSAGE = (
    "Gracias por escribir de nuevo. Voy a avisarle a nuestro equipo para que "
    "retome tu conversación."
)


@dataclass
class OrchestratorResult:
    conversation_id: str
    status: ConversationStatus
    reply_text: str | None
    classification: IntentClassificationResult | None
    tools_used: list[str]
    awaiting_human: bool


def _history_for_gemini(conversation: Conversation) -> list[dict[str, str]]:
    # Keep it short: enough context for a natural reply, not a full replay
    # of the conversation on every turn (cost + latency).
    recent = conversation.messages[-6:] if conversation.messages else []
    history = []
    for m in recent:
        role = "bot" if m.sender == MessageSender.BOT else "patient"
        if m.body:
            history.append({"role": role, "text": m.body})
    return history


def _handoff(
    db: Session,
    conversation: Conversation,
    *,
    reason: str,
    priority: str,
    patient_message: str,
) -> str:
    apply_transition(conversation, ConversationEvent.HANDOFF_TRIGGERED, Actor.BOT)
    conversation.handoff_reason = reason
    conversation.handoff_priority = priority
    crm.record_outbound_message(db, conversation, patient_message, sender=MessageSender.BOT)
    return patient_message


def process_inbound_message(
    db: Session,
    tenant: Tenant,
    phone: str,
    body: str,
    *,
    twilio_message_id: str | None = None,
    whatsapp_profile_name: str | None = None,
    gemini_client=None,
) -> OrchestratorResult:
    contact = crm.get_or_create_contact(db, tenant, phone, whatsapp_profile_name=whatsapp_profile_name)
    conversation = crm.get_or_create_open_conversation(db, tenant, contact)

    # Already deduplicated on twilio_message_id inside record_inbound_message.
    crm.record_inbound_message(db, conversation, body, twilio_message_id=twilio_message_id)

    status_before = conversation.status
    new_status = apply_transition(conversation, ConversationEvent.PATIENT_MESSAGE_RECEIVED, Actor.SYSTEM)

    # The patient is mid-conversation with a human, or just got routed back
    # to one from HUMAN_RESOLVED — the bot stays completely silent either
    # way. This is the enforcement point for "cómo evitar respuestas
    # simultáneas de bot y humano" (architecture doc §11): the orchestrator
    # simply never reaches the reply-generation code below in these states.
    if new_status in (ConversationStatus.WAITING_FOR_HUMAN, ConversationStatus.HUMAN_ACTIVE):
        reply_text = None
        if status_before == ConversationStatus.HUMAN_RESOLVED:
            crm.record_outbound_message(db, conversation, REOPENED_AFTER_RESOLVED_MESSAGE, sender=MessageSender.BOT)
            reply_text = REOPENED_AFTER_RESOLVED_MESSAGE
        return OrchestratorResult(
            conversation_id=str(conversation.id),
            status=conversation.status,
            reply_text=reply_text,
            classification=None,
            tools_used=[],
            awaiting_human=True,
        )

    # --- Bot is active: safety check + intent classification + routing ---
    classification = classify_intent(tenant, body, gemini_client=gemini_client)

    for msg in conversation.messages[-1:]:
        msg.intent = classification.primary_intent.value

    if classification.category == IntentCategory.C:
        if classification.safety_category is not None:
            action = resolve_protocol_action(tenant, classification.safety_category, classification.priority)
            patient_message = action.patient_message
            reason = action.handoff_note
        else:  # HUMAN_REQUEST: the patient just asked for a person, nothing to protocol-resolve
            patient_message = GENERIC_ADMIN_HANDOFF_MESSAGE
            reason = f"Patient explicitly requested a human agent (intent {classification.primary_intent.value})."

        reply_text = _handoff(db, conversation, reason=reason, priority=classification.priority.value, patient_message=patient_message)
        tools_used: list[str] = []

    elif classification.category == IntentCategory.B:
        reason = f"Category B request: {classification.primary_intent.value}"
        reply_text = _handoff(
            db, conversation, reason=reason, priority=classification.priority.value, patient_message=GENERIC_ADMIN_HANDOFF_MESSAGE
        )
        tools_used = []

    elif classification.category == IntentCategory.A:
        if gemini_client is None:
            reply_text = (
                "Ahora mismo no puedo consultar esa información automáticamente. "
                "Te conecto con el equipo."
            )
            reply_text = _handoff(
                db,
                conversation,
                reason="Gemini client unavailable for a category-A intent",
                priority="MEDIUM",
                patient_message=reply_text,
            )
            tools_used = []
        else:
            try:
                answer = gemini_client.answer(
                    tenant=tenant,
                    intent=classification.primary_intent.value,
                    user_message=body,
                    history=_history_for_gemini(conversation),
                )
                reply_text = answer.text
                tools_used = answer.tools_used
                crm.record_outbound_message(db, conversation, reply_text, sender=MessageSender.BOT)
            except Exception:
                # Fail closed: any Gemini failure (network, unsupported
                # intent, malformed response) becomes a human handoff,
                # never a guess or an empty reply.
                reply_text = _handoff(
                    db,
                    conversation,
                    reason=f"Gemini failed to answer category-A intent {classification.primary_intent.value}",
                    priority="MEDIUM",
                    patient_message=GENERIC_ADMIN_HANDOFF_MESSAGE,
                )
                tools_used = []

    else:  # OTHER_UNCLEAR
        reply_text = GENERIC_UNCLEAR_RETRY_MESSAGE
        apply_transition(conversation, ConversationEvent.BOT_ASKS_CLARIFICATION, Actor.BOT)
        crm.record_outbound_message(db, conversation, reply_text, sender=MessageSender.BOT)
        tools_used = []

    return OrchestratorResult(
        conversation_id=str(conversation.id),
        status=conversation.status,
        reply_text=reply_text,
        classification=classification,
        tools_used=tools_used,
        awaiting_human=conversation.status == ConversationStatus.WAITING_FOR_HUMAN,
    )
