from types import SimpleNamespace

from app.models.message import MessageSender
from app.services.orchestrator import (
    ESCALATED_NOTICE_MESSAGE,
    REOPENED_AFTER_RESOLVED_MESSAGE,
    _should_send_escalated_notice,
)


def _msg(sender, body="x"):
    return SimpleNamespace(sender=sender, body=body)


def _conv(*messages):
    return SimpleNamespace(messages=list(messages))


def test_first_message_after_handoff_gets_notice():
    conv = _conv(_msg(MessageSender.BOT, "Te conecto con el equipo."), _msg(MessageSender.PATIENT))
    assert _should_send_escalated_notice(conv)


def test_only_once():
    conv = _conv(
        _msg(MessageSender.BOT, "Te conecto con el equipo."),
        _msg(MessageSender.PATIENT),
        _msg(MessageSender.BOT, ESCALATED_NOTICE_MESSAGE),
        _msg(MessageSender.PATIENT),
    )
    assert not _should_send_escalated_notice(conv)


def test_silent_after_a_person_wrote():
    conv = _conv(_msg(MessageSender.HUMAN_AGENT), _msg(MessageSender.PATIENT))
    assert not _should_send_escalated_notice(conv)


def test_silent_after_reopened_message():
    conv = _conv(_msg(MessageSender.BOT, REOPENED_AFTER_RESOLVED_MESSAGE), _msg(MessageSender.PATIENT))
    assert not _should_send_escalated_notice(conv)


def test_new_escalation_gets_a_new_notice():
    conv = _conv(
        _msg(MessageSender.BOT, ESCALATED_NOTICE_MESSAGE),
        _msg(MessageSender.BOT, "Respuesta normal del bot."),
        _msg(MessageSender.BOT, "Te conecto con el equipo."),
        _msg(MessageSender.PATIENT),
    )
    assert _should_send_escalated_notice(conv)