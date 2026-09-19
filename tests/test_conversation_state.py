"""
Pure-logic tests for the conversation state machine — no DB needed, since
apply_transition only mutates the in-memory object passed to it.

Run with:
    .venv/bin/python -m tests.test_conversation_state
"""

from app.models.conversation import Conversation, ConversationStatus
from app.services.conversation_state import (
    Actor,
    ConversationEvent,
    InvalidTransitionError,
    UnauthorizedActorError,
    apply_transition,
)


def _conv(status: ConversationStatus) -> Conversation:
    c = Conversation()
    c.status = status
    c.resolved_at = None
    return c


def test_patient_message_resumes_bot_after_clarification() -> None:
    c = _conv(ConversationStatus.WAITING_FOR_PATIENT)
    new_status = apply_transition(c, ConversationEvent.PATIENT_MESSAGE_RECEIVED, Actor.SYSTEM)
    assert new_status == ConversationStatus.BOT_ACTIVE
    print("PASS: test_patient_message_resumes_bot_after_clarification")


def test_patient_message_during_human_active_does_not_change_state() -> None:
    c = _conv(ConversationStatus.HUMAN_ACTIVE)
    new_status = apply_transition(c, ConversationEvent.PATIENT_MESSAGE_RECEIVED, Actor.SYSTEM)
    assert new_status == ConversationStatus.HUMAN_ACTIVE
    print("PASS: test_patient_message_during_human_active_does_not_change_state")


def test_patient_message_after_resolved_reopens_to_human_not_bot() -> None:
    c = _conv(ConversationStatus.HUMAN_RESOLVED)
    new_status = apply_transition(c, ConversationEvent.PATIENT_MESSAGE_RECEIVED, Actor.SYSTEM)
    assert new_status == ConversationStatus.WAITING_FOR_HUMAN
    print("PASS: test_patient_message_after_resolved_reopens_to_human_not_bot")


def test_bot_resumed_folds_into_bot_active_on_next_message() -> None:
    c = _conv(ConversationStatus.BOT_RESUMED)
    new_status = apply_transition(c, ConversationEvent.PATIENT_MESSAGE_RECEIVED, Actor.SYSTEM)
    assert new_status == ConversationStatus.BOT_ACTIVE
    print("PASS: test_bot_resumed_folds_into_bot_active_on_next_message")


def test_full_handoff_lifecycle() -> None:
    c = _conv(ConversationStatus.BOT_ACTIVE)

    assert apply_transition(c, ConversationEvent.HANDOFF_TRIGGERED, Actor.BOT) == ConversationStatus.WAITING_FOR_HUMAN
    assert apply_transition(c, ConversationEvent.AGENT_CLAIMED, Actor.HUMAN_AGENT) == ConversationStatus.HUMAN_ACTIVE

    apply_transition(c, ConversationEvent.AGENT_RESOLVED, Actor.HUMAN_AGENT)
    assert c.status == ConversationStatus.HUMAN_RESOLVED
    assert c.resolved_at is not None

    new_status = apply_transition(c, ConversationEvent.BOT_REACTIVATED, Actor.HUMAN_AGENT)
    assert new_status == ConversationStatus.BOT_RESUMED
    print("PASS: test_full_handoff_lifecycle")


def test_new_escalation_clears_stale_resolved_at() -> None:
    c = _conv(ConversationStatus.BOT_ACTIVE)
    c.resolved_at = "2020-01-01T00:00:00+00:00"  # stale leftover from a much earlier episode
    apply_transition(c, ConversationEvent.HANDOFF_TRIGGERED, Actor.BOT)
    assert c.status == ConversationStatus.WAITING_FOR_HUMAN
    assert c.resolved_at is None
    print("PASS: test_new_escalation_clears_stale_resolved_at")


def test_bot_cannot_trigger_agent_only_events() -> None:
    c = _conv(ConversationStatus.WAITING_FOR_HUMAN)
    try:
        apply_transition(c, ConversationEvent.AGENT_CLAIMED, Actor.BOT)
    except UnauthorizedActorError:
        pass
    else:
        raise AssertionError("bot must not be able to claim a handoff")
    print("PASS: test_bot_cannot_trigger_agent_only_events")


def test_patient_actor_can_never_trigger_a_transition_directly() -> None:
    c = _conv(ConversationStatus.BOT_ACTIVE)
    try:
        apply_transition(c, ConversationEvent.PATIENT_MESSAGE_RECEIVED, Actor.PATIENT)
    except UnauthorizedActorError:
        pass
    else:
        raise AssertionError("only SYSTEM (the webhook, on the patient's behalf) may record this event")
    print("PASS: test_patient_actor_can_never_trigger_a_transition_directly")


def test_invalid_transition_from_closed_is_rejected() -> None:
    c = _conv(ConversationStatus.CLOSED)
    try:
        apply_transition(c, ConversationEvent.PATIENT_MESSAGE_RECEIVED, Actor.SYSTEM)
    except InvalidTransitionError:
        pass
    else:
        raise AssertionError("CLOSED must be terminal; a new message needs a NEW conversation, not a reopen")
    print("PASS: test_invalid_transition_from_closed_is_rejected")


def test_agent_cannot_claim_an_already_active_conversation() -> None:
    c = _conv(ConversationStatus.HUMAN_ACTIVE)
    try:
        apply_transition(c, ConversationEvent.AGENT_CLAIMED, Actor.HUMAN_AGENT)
    except InvalidTransitionError:
        pass
    else:
        raise AssertionError("a conversation already HUMAN_ACTIVE cannot be claimed again")
    print("PASS: test_agent_cannot_claim_an_already_active_conversation")


if __name__ == "__main__":
    test_patient_message_resumes_bot_after_clarification()
    test_patient_message_during_human_active_does_not_change_state()
    test_patient_message_after_resolved_reopens_to_human_not_bot()
    test_bot_resumed_folds_into_bot_active_on_next_message()
    test_full_handoff_lifecycle()
    test_new_escalation_clears_stale_resolved_at()
    test_bot_cannot_trigger_agent_only_events()
    test_patient_actor_can_never_trigger_a_transition_directly()
    test_invalid_transition_from_closed_is_rejected()
    test_agent_cannot_claim_an_already_active_conversation()
    print("\nAll conversation state machine tests passed.")
