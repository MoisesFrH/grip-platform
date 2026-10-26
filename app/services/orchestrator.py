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
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.appointment import ReminderResponse
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import MessageSender
from app.models.tenant import Tenant
from app.services import appointments, crm, greeting, intake, payments
from app.services.conversation_state import Actor, ConversationEvent, apply_transition
from app.services.intent_classifier import IntentClassificationResult, classify_intent
from app.services.intents import Intent, IntentCategory
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


_HANDOFF_PROMISES = (
    "te conecto con", "te paso con", "te derivo", "te comunico con", "te pongo en contacto",
    "conecto con el equipo", "te contactará el equipo", "te contactara el equipo",
)


def _promises_handoff(text: str) -> bool:
    """True when the bot's own answer says it will pass the patient to a
    person. Without this the CRM never gets a case to claim."""
    lowered = (text or "").lower()
    return any(phrase in lowered for phrase in _HANDOFF_PROMISES)


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
    # Only the FIRST handoff counts for "time to claim" — a conversation
    # that escalates, gets resolved, then escalates again shouldn't have
    # this overwritten, or the metric would understate how long the first,
    # possibly more urgent, wait actually was.
    if conversation.handoff_triggered_at is None:
        conversation.handoff_triggered_at = datetime.now(timezone.utc)
    crm.record_outbound_message(db, conversation, patient_message, sender=MessageSender.BOT)
    return patient_message


def _remember_name(conversation: Conversation) -> None:
    """The name given during new-patient registration becomes the contact's
    name in the CRM (only if staff have not set one), so the bot can greet by
    name from then on. It never overwrites a name a staff member typed."""
    name = ((conversation.intake_data or {}).get("patient_name") or "").strip()
    contact = conversation.contact
    if name and contact is not None and not contact.name:
        contact.name = name[:120]


def _intake_turn(
    db: Session,
    conversation: Conversation,
    turn: "intake.IntakeTurn",
    classification: IntentClassificationResult | None = None,
) -> OrchestratorResult:
    _remember_name(conversation)
    if turn.complete:
        _handoff(db, conversation, reason=turn.reason, priority="MEDIUM", patient_message=turn.reply)
    else:
        apply_transition(conversation, ConversationEvent.BOT_ASKS_CLARIFICATION, Actor.BOT)
        crm.record_outbound_message(db, conversation, turn.reply, sender=MessageSender.BOT)
    return OrchestratorResult(
        conversation_id=str(conversation.id),
        status=conversation.status,
        reply_text=turn.reply,
        classification=classification,
        tools_used=[],
        awaiting_human=conversation.status == ConversationStatus.WAITING_FOR_HUMAN,
    )


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

    # --- Reminder-reply guard: runs BEFORE intent classification, on
    # purpose. If this contact has an appointment reminder awaiting a
    # reply and this message reads like a cancel/reschedule request (a
    # deterministic keyword check — see app.services.appointments), that
    # always goes to a human, full stop. This does not wait for Gemini to
    # classify it as APPOINTMENT_CHANGE/APPOINTMENT_CANCEL — a
    # misclassification there would mean the bot quietly no-ops on a
    # patient trying to cancel or move their appointment, which is
    # exactly the failure mode this guard exists to rule out.
    pending_appointment = appointments.find_pending_reminder_for_contact(db, tenant, contact)
    if pending_appointment is not None:
        reminder_signal = appointments.classify_reminder_reply(body)

        # Payment question in the reminder. A cancel/reschedule signal always
        # wins (checked first by classify_reminder_reply / wants_to_pay).
        if reminder_signal != ReminderResponse.CANCEL_OR_RESCHEDULE:
            if appointments.says_already_paid(body):
                for msg in conversation.messages[-1:]:
                    msg.intent = "PAYMENT_NOTICE"
                payments.mark_reported(db, tenant, pending_appointment)
                reply_text = _handoff(
                    db,
                    conversation,
                    reason="Paciente avisa que ya pagó por transferencia: verificar el pago.",
                    priority="MEDIUM",
                    patient_message=appointments.PAYMENT_RECEIVED_HANDOFF_MESSAGE,
                )
                return OrchestratorResult(
                    conversation_id=str(conversation.id),
                    status=conversation.status,
                    reply_text=reply_text,
                    classification=None,
                    tools_used=[],
                    awaiting_human=True,
                )
            if appointments.wants_to_pay(body):
                for msg in conversation.messages[-1:]:
                    msg.intent = "PAYMENT_REQUEST"
                if reminder_signal == ReminderResponse.CONFIRMED:
                    appointments.apply_reminder_reply(pending_appointment, reminder_signal)
                reply_text = appointments.payment_details_message(tenant)
                crm.record_outbound_message(db, conversation, reply_text, sender=MessageSender.BOT)
                return OrchestratorResult(
                    conversation_id=str(conversation.id),
                    status=conversation.status,
                    reply_text=reply_text,
                    classification=None,
                    tools_used=[],
                    awaiting_human=False,
                )

        if reminder_signal is not None:
            appointments.apply_reminder_reply(pending_appointment, reminder_signal)
            for msg in conversation.messages[-1:]:
                msg.intent = "APPOINTMENT_REMINDER_REPLY"
            if reminder_signal.value == "CANCEL_OR_RESCHEDULE":
                reply_text = _handoff(
                    db,
                    conversation,
                    reason="Patient replied to an appointment reminder with a cancel/reschedule request.",
                    priority="HIGH",
                    patient_message=appointments.RESCHEDULE_HANDOFF_MESSAGE,
                )
                return OrchestratorResult(
                    conversation_id=str(conversation.id),
                    status=conversation.status,
                    reply_text=reply_text,
                    classification=None,
                    tools_used=[],
                    awaiting_human=True,
                )
            else:  # CONFIRMED
                # Short-circuits here rather than falling through to intent
                # classification: a bare "sí" / "confirmado" has nothing
                # else for the classifier to work with, and would otherwise
                # land on OTHER_UNCLEAR and trigger an unnecessary
                # clarifying question right after the patient already gave
                # a clear answer.
                reply_text = appointments.APPOINTMENT_CONFIRMED_REPLY
                crm.record_outbound_message(db, conversation, reply_text, sender=MessageSender.BOT)
                return OrchestratorResult(
                    conversation_id=str(conversation.id),
                    status=conversation.status,
                    reply_text=reply_text,
                    classification=None,
                    tools_used=[],
                    awaiting_human=False,
                )

    # --- New-patient intake in progress: deterministic, no LLM. Only the
    # safety layer, a request for a human, or a question can break out of it
    # (they continue into the normal pipeline below).
    if intake.is_active(conversation):
        if intake.should_interrupt(tenant, body, conversation.intake_data["step"]):
            intake.abandon(conversation)
        else:
            for msg in conversation.messages[-1:]:
                msg.intent = "NEW_PATIENT_INQUIRY"
            return _intake_turn(db, conversation, intake.advance(tenant, conversation, body))

    # --- A bare greeting ("hola", "buenas tardes"): welcome and ask what
    # they need, instead of the "no entendí" retry. Only a message that is
    # entirely a greeting gets here, so nothing safety-relevant is skipped.
    if greeting.is_greeting(body):
        # Personalised when the CRM knows the person: their name (set by
        # staff) and, if they have one coming up, their next appointment.
        upcoming = appointments.next_appointment_for_contact(db, tenant, contact) if contact.name else None
        reply_text = greeting.welcome_message(
            tenant,
            name=contact.name,
            next_appointment=appointments.describe_appointment(upcoming) if upcoming else None,
        )
        for msg in conversation.messages[-1:]:
            msg.intent = "GREETING"
        crm.record_outbound_message(db, conversation, reply_text, sender=MessageSender.BOT)
        return OrchestratorResult(
            conversation_id=str(conversation.id),
            status=conversation.status,
            reply_text=reply_text,
            classification=None,
            tools_used=[],
            awaiting_human=False,
        )

    # --- Bot is active: safety check + intent classification + routing ---
    classification = classify_intent(tenant, body, gemini_client=gemini_client)

    for msg in conversation.messages[-1:]:
        msg.intent = classification.primary_intent.value
        if classification.safety_category is not None:
            # Not surfaced to the patient or the agent inbox — this is
            # purely so the dashboard (app.services.analytics) can break
            # down safety handoffs by category without re-deriving them
            # from free-text handoff_reason strings.
            msg.extra_metadata = {"safety_category": classification.safety_category.value}

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

    elif classification.category == IntentCategory.B and (
        classification.primary_intent == Intent.NEW_PATIENT_INQUIRY and intake.get_config(tenant)
    ):
        return _intake_turn(db, conversation, intake.start(tenant, conversation, body), classification)

    elif classification.category == IntentCategory.B:
        reason = f"Category B request: {classification.primary_intent.value}"
        reply_text = _handoff(
            db, conversation, reason=reason, priority=classification.priority.value, patient_message=GENERIC_ADMIN_HANDOFF_MESSAGE
        )
        tools_used = []

    elif (
        classification.primary_intent == Intent.BOOKING_GUIDANCE and intake.get_config(tenant)
    ):
        # "¿Cómo agendo?": a person does the registration, so start the intake.
        return _intake_turn(db, conversation, intake.start(tenant, conversation, body), classification)

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
                if _promises_handoff(reply_text):
                    # The bot told the patient "te conecto con el equipo": make
                    # it true, so the case shows up in the CRM with "Tomar".
                    reply_text = _handoff(
                        db,
                        conversation,
                        reason=f"El bot no tenía el dato ({classification.primary_intent.value}) y ofreció pasar con el equipo",
                        priority="MEDIUM",
                        patient_message=reply_text,
                    )
                else:
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