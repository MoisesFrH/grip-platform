"""
Conversation state machine (architecture doc §11 / brief §6).

States (defined on the ORM model, app.models.conversation.ConversationStatus):

  BOT_ACTIVE            The bot handles messages automatically.
  WAITING_FOR_PATIENT    The bot asked something and is waiting on the patient
                        (a clarifying question, or data needed for a
                        category-B task like a new appointment date).
  WAITING_FOR_HUMAN      Handed off; no agent has claimed it yet.
  HUMAN_ACTIVE           An agent has taken over. The bot MUST NOT reply.
  HUMAN_RESOLVED         The agent marked their part done, but has not
                        explicitly handed control back to the bot yet.
  BOT_RESUMED            An agent just pressed "return to AI". Behaves like
                        BOT_ACTIVE for message routing; kept as a distinct
                        value only so the audit trail shows a conversation
                        came back from a human rather than started fresh.
                        The very next processed message folds it into
                        BOT_ACTIVE.
  CLOSED                 Terminal. Never transitioned out of — a patient
                        writing again after CLOSED gets a brand NEW
                        conversation (see app.services.crm), not a reopened
                        one, so historical records stay immutable.

Who can change what (architecture doc §6's "quién puede cambiarlo"):
  BOT           can ask a clarifying question, or trigger a handoff.
  PATIENT       triggers events indirectly by messaging; never sets state.
  HUMAN_AGENT   can claim, resolve, hand back to the bot, or close.
  SYSTEM        automated jobs — timeouts, closing stale conversations.
Every event below is gated to the actor(s) who are allowed to trigger it;
apply_transition raises UnauthorizedActorError otherwise. This is the
enforcement point that stops the bot from ever "deciding" a human-owned
transition (e.g. marking a handoff resolved) and vice versa.

Concurrency: this module only computes the next state — it does not open
a transaction. The caller (app.services.crm) is responsible for locking
the conversation row (SELECT ... FOR UPDATE) before calling
apply_transition and committing after, which is what actually prevents
two concurrent webhook deliveries or a bot/human race from interleaving.
"""

import enum
from datetime import datetime, timezone

from app.models.conversation import Conversation, ConversationStatus


class Actor(str, enum.Enum):
    BOT = "BOT"
    PATIENT = "PATIENT"
    HUMAN_AGENT = "HUMAN_AGENT"
    SYSTEM = "SYSTEM"


class ConversationEvent(str, enum.Enum):
    # A message arrived from the patient. Effect depends entirely on the
    # current state (see TRANSITIONS) — this event alone never implies
    # "the bot should respond automatically now".
    PATIENT_MESSAGE_RECEIVED = "PATIENT_MESSAGE_RECEIVED"

    BOT_ASKS_CLARIFICATION = "BOT_ASKS_CLARIFICATION"
    HANDOFF_TRIGGERED = "HANDOFF_TRIGGERED"
    AGENT_CLAIMED = "AGENT_CLAIMED"
    AGENT_RESOLVED = "AGENT_RESOLVED"
    BOT_REACTIVATED = "BOT_REACTIVATED"  # the "return to AI" button
    PATIENT_TIMEOUT = "PATIENT_TIMEOUT"  # patient never answered the bot's question
    CONVERSATION_CLOSED = "CONVERSATION_CLOSED"


class InvalidTransitionError(Exception):
    """Raised when `event` has no defined effect from the conversation's
    current status — a bug in the caller, never something to paper over
    with a default state."""


class UnauthorizedActorError(Exception):
    """Raised when `actor` is not allowed to trigger `event` at all,
    regardless of the current status."""


EVENT_ACTORS: dict[ConversationEvent, set[Actor]] = {
    ConversationEvent.PATIENT_MESSAGE_RECEIVED: {Actor.SYSTEM},  # the webhook records it on the patient's behalf
    ConversationEvent.BOT_ASKS_CLARIFICATION: {Actor.BOT},
    ConversationEvent.HANDOFF_TRIGGERED: {Actor.BOT, Actor.SYSTEM},
    ConversationEvent.AGENT_CLAIMED: {Actor.HUMAN_AGENT},
    ConversationEvent.AGENT_RESOLVED: {Actor.HUMAN_AGENT},
    ConversationEvent.BOT_REACTIVATED: {Actor.HUMAN_AGENT},
    ConversationEvent.PATIENT_TIMEOUT: {Actor.SYSTEM},
    ConversationEvent.CONVERSATION_CLOSED: {Actor.HUMAN_AGENT, Actor.SYSTEM},
}

S = ConversationStatus
E = ConversationEvent

# (current_status, event) -> new_status. A `None` new_status means "valid,
# but no status change" (e.g. the patient messaging again while an agent
# already has the conversation). A missing entry means the event cannot
# fire from that status at all -> InvalidTransitionError.
TRANSITIONS: dict[tuple[ConversationStatus, ConversationEvent], ConversationStatus | None] = {
    # --- inbound patient messages, per current status ---
    (S.BOT_ACTIVE, E.PATIENT_MESSAGE_RECEIVED): None,
    (S.WAITING_FOR_PATIENT, E.PATIENT_MESSAGE_RECEIVED): S.BOT_ACTIVE,
    (S.WAITING_FOR_HUMAN, E.PATIENT_MESSAGE_RECEIVED): None,
    (S.HUMAN_ACTIVE, E.PATIENT_MESSAGE_RECEIVED): None,
    # Deliberately conservative: a message after HUMAN_RESOLVED goes back
    # to a human queue rather than silently letting the bot pick it up —
    # "resolved" was about a specific issue, and only an explicit
    # BOT_REACTIVATED ("return to AI") should let the bot answer again.
    (S.HUMAN_RESOLVED, E.PATIENT_MESSAGE_RECEIVED): S.WAITING_FOR_HUMAN,
    (S.BOT_RESUMED, E.PATIENT_MESSAGE_RECEIVED): S.BOT_ACTIVE,
    # CLOSED has no entry on purpose: app.services.crm never calls
    # apply_transition on a CLOSED conversation — it opens a NEW one.
    # --- bot-driven ---
    (S.BOT_ACTIVE, E.BOT_ASKS_CLARIFICATION): S.WAITING_FOR_PATIENT,
    (S.BOT_ACTIVE, E.HANDOFF_TRIGGERED): S.WAITING_FOR_HUMAN,
    (S.WAITING_FOR_PATIENT, E.HANDOFF_TRIGGERED): S.WAITING_FOR_HUMAN,
    (S.BOT_RESUMED, E.HANDOFF_TRIGGERED): S.WAITING_FOR_HUMAN,
    (S.WAITING_FOR_PATIENT, E.PATIENT_TIMEOUT): S.CLOSED,
    # --- human-agent-driven ---
    (S.WAITING_FOR_HUMAN, E.AGENT_CLAIMED): S.HUMAN_ACTIVE,
    (S.HUMAN_ACTIVE, E.AGENT_RESOLVED): S.HUMAN_RESOLVED,
    (S.HUMAN_ACTIVE, E.BOT_REACTIVATED): S.BOT_RESUMED,
    (S.HUMAN_RESOLVED, E.BOT_REACTIVATED): S.BOT_RESUMED,
    # --- closing, from any non-terminal status ---
    (S.BOT_ACTIVE, E.CONVERSATION_CLOSED): S.CLOSED,
    (S.WAITING_FOR_PATIENT, E.CONVERSATION_CLOSED): S.CLOSED,
    (S.WAITING_FOR_HUMAN, E.CONVERSATION_CLOSED): S.CLOSED,
    (S.HUMAN_ACTIVE, E.CONVERSATION_CLOSED): S.CLOSED,
    (S.HUMAN_RESOLVED, E.CONVERSATION_CLOSED): S.CLOSED,
    (S.BOT_RESUMED, E.CONVERSATION_CLOSED): S.CLOSED,
}


def apply_transition(conversation: Conversation, event: ConversationEvent, actor: Actor) -> ConversationStatus:
    """
    Validates and applies one state transition in place. Does NOT commit —
    the caller holds the row lock and the transaction (app.services.crm).

    Raises UnauthorizedActorError if `actor` may never trigger `event`,
    InvalidTransitionError if `event` has no effect from the conversation's
    current status.
    """
    if actor not in EVENT_ACTORS[event]:
        raise UnauthorizedActorError(f"{actor.value} may not trigger {event.value}")

    key = (conversation.status, event)
    if key not in TRANSITIONS:
        raise InvalidTransitionError(
            f"Event {event.value} has no defined transition from status {conversation.status.value}"
        )

    new_status = TRANSITIONS[key]
    now = datetime.now(timezone.utc)

    if new_status is not None and new_status != conversation.status:
        conversation.status = new_status

        if new_status == ConversationStatus.HUMAN_RESOLVED:
            conversation.resolved_at = now
        elif new_status == ConversationStatus.CLOSED and conversation.resolved_at is None:
            conversation.resolved_at = now
        elif new_status in (ConversationStatus.WAITING_FOR_HUMAN,):
            # A fresh escalation clears any earlier resolution timestamp —
            # this is a new episode needing a human, not the old one reopening.
            conversation.resolved_at = None

    return conversation.status
