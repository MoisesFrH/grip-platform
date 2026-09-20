"""
Tests for the simulator's "Probar recordatorio" + voice-note prototype
integration (app/api/routes/simulator.py):
  - include_voice=False (the default) behaves exactly as before — a pure
    regression check, since this flag was added to an existing endpoint.
  - include_voice=True attaches a second, separate message carrying the
    audio (base64, in that message's own extra_metadata), and that audio
    can be fetched back via GET /simulator/messages/{id}/voice-note.
  - A synthesis failure reports voice_error on the response WITHOUT
    breaking the text reminder that already succeeded.

voice_reminders.synthesize_reminder_voice is monkeypatched — this is a
route-wiring test, not a re-test of the TTS/ffmpeg pipeline itself (that's
tests/test_voice_reminders.py).

Run with:
    .venv/bin/python -m pytest tests/test_simulator_voice_reminder.py
"""

import uuid

from fastapi import HTTPException

from app.api.routes import simulator
from app.core.db import SessionLocal
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message


def _test_phone() -> str:
    return f"+1555{uuid.uuid4().int % 10_000_000:07d}"


def _cleanup(db, phone: str) -> None:
    contact = db.query(Contact).filter(Contact.phone == phone).one_or_none()
    if contact is None:
        return
    conversation_ids = [c.id for c in db.query(Conversation).filter(Conversation.contact_id == contact.id)]
    if conversation_ids:
        db.query(Message).filter(Message.conversation_id.in_(conversation_ids)).delete(synchronize_session=False)
    db.query(Conversation).filter(Conversation.contact_id == contact.id).delete(synchronize_session=False)
    db.query(Contact).filter(Contact.id == contact.id).delete(synchronize_session=False)
    db.commit()


def test_test_reminder_without_voice_is_unaffected():
    db = SessionLocal()
    phone = _test_phone()
    try:
        result = simulator.send_test_reminder(
            simulator.TestReminderRequest(phone=phone), db
        )
        assert result.reminder_text
        assert result.voice_message_id is None
        assert result.voice_error is None
    finally:
        _cleanup(db, phone)
        db.close()


def test_test_reminder_with_voice_attaches_playable_message(monkeypatch):
    db = SessionLocal()
    phone = _test_phone()
    monkeypatch.setattr(simulator.voice_reminders, "synthesize_reminder_voice", lambda text, **kw: b"OggS-fake-audio")
    try:
        result = simulator.send_test_reminder(
            simulator.TestReminderRequest(phone=phone, include_voice=True), db
        )
        assert result.voice_error is None
        assert result.voice_message_id is not None

        response = simulator.get_voice_note_audio(result.voice_message_id, db)
        assert response.media_type == "audio/ogg"
        assert response.body == b"OggS-fake-audio"

        # The list-messages path (what the chat pane actually polls) must
        # flag this message so the frontend knows to show the play button.
        conversation = db.query(Conversation).filter(Conversation.id == result.conversation_id).one()
        messages_out = [simulator._message_out(m) for m in conversation.messages]
        voice_flags = [m.has_voice_note for m in messages_out]
        assert True in voice_flags
        # The plain text reminder (sent first) must NOT be flagged.
        assert False in voice_flags
    finally:
        _cleanup(db, phone)
        db.close()


def test_test_reminder_voice_failure_does_not_break_text_reminder(monkeypatch):
    db = SessionLocal()
    phone = _test_phone()

    def failing_synthesize(text, **kw):
        raise simulator.voice_reminders.VoiceSynthesisError("no network in this sandbox")

    monkeypatch.setattr(simulator.voice_reminders, "synthesize_reminder_voice", failing_synthesize)
    try:
        result = simulator.send_test_reminder(
            simulator.TestReminderRequest(phone=phone, include_voice=True), db
        )
        # The text reminder still went through — voice is only ever additive.
        assert result.reminder_text
        assert result.appointment_id
        assert result.voice_message_id is None
        assert "no network in this sandbox" in result.voice_error
    finally:
        _cleanup(db, phone)
        db.close()


def test_get_voice_note_audio_404s_for_message_without_voice():
    db = SessionLocal()
    phone = _test_phone()
    try:
        result = simulator.send_test_reminder(simulator.TestReminderRequest(phone=phone), db)
        conversation = db.query(Conversation).filter(Conversation.id == result.conversation_id).one()
        text_message = conversation.messages[0]
        try:
            simulator.get_voice_note_audio(str(text_message.id), db)
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 404
    finally:
        _cleanup(db, phone)
        db.close()


def test_get_voice_note_audio_404s_for_unknown_message_id():
    db = SessionLocal()
    try:
        try:
            simulator.get_voice_note_audio(str(uuid.uuid4()), db)
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 404
    finally:
        db.close()
