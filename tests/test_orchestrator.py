"""
End-to-end offline tests for the orchestrator + human inbox — the full
pipeline (contact -> conversation -> safety/intent -> reply/handoff) minus
the actual Gemini network call, which this sandbox can't reach. Run with:

    .venv/bin/python -m tests.test_orchestrator
"""

import uuid

from app.core.db import SessionLocal
from app.models.contact import Contact
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message, MessageSender
from app.models.tenant import Tenant
from app.services import human_inbox
from app.services.gemini_client import GeminiAnswer
from app.services.orchestrator import process_inbound_message


class FakeGeminiClient:
    """Duck-types GeminiClient's public surface for offline testing.
    Configure `answer_text`/`answer_tools` per test; classify_intent is
    only reached on the zero/ambiguous-match path, so most tests never
    need it and it raises if accidentally called."""

    def __init__(self, answer_text: str = "respuesta de prueba", answer_tools: list[str] | None = None, classify_result: str | None = None):
        self.answer_text = answer_text
        self.answer_tools = answer_tools or []
        self.classify_result = classify_result
        self.answer_calls: list[dict] = []
        self.classify_calls: list[dict] = []

    def answer(self, *, tenant, intent, user_message, history=None):
        self.answer_calls.append({"tenant": tenant, "intent": intent, "user_message": user_message})
        return GeminiAnswer(text=self.answer_text, tools_used=self.answer_tools, tool_results={}, usage={})

    def classify_intent(self, *, candidate_intents, user_message):
        self.classify_calls.append({"candidate_intents": candidate_intents, "user_message": user_message})
        if self.classify_result is None:
            raise AssertionError("classify_intent should not have been called for this scenario")
        return self.classify_result


def _grip() -> Tenant:
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == "grip").one()
    db.close()
    return tenant


def _test_phone() -> str:
    return f"+1555{uuid.uuid4().int % 10_000_000:07d}"


def _cleanup(db, contact_id) -> None:
    db.query(Message).filter(Message.conversation_id.in_(
        db.query(Conversation.id).filter(Conversation.contact_id == contact_id)
    )).delete(synchronize_session=False)
    db.query(Conversation).filter(Conversation.contact_id == contact_id).delete(synchronize_session=False)
    db.query(Contact).filter(Contact.id == contact_id).delete(synchronize_session=False)
    db.commit()


def test_category_a_message_gets_a_grounded_bot_reply() -> None:
    db = SessionLocal()
    tenant = _grip()
    phone = _test_phone()
    fake_gemini = FakeGeminiClient(answer_text="Estamos en Quito, cerca del metro.", answer_tools=["get_location"])

    try:
        result = process_inbound_message(db, tenant, phone, "¿cómo llego al consultorio?", gemini_client=fake_gemini)
        db.commit()

        assert result.status == ConversationStatus.BOT_ACTIVE
        assert result.reply_text == "Estamos en Quito, cerca del metro."
        assert result.tools_used == ["get_location"]
        assert result.awaiting_human is False
        assert len(fake_gemini.answer_calls) == 1
        assert fake_gemini.answer_calls[0]["intent"] == "LOCATION"

        contact = db.query(Contact).filter(Contact.tenant_id == tenant.id, Contact.phone == phone).one()
        _cleanup(db, contact.id)
        print("PASS: test_category_a_message_gets_a_grounded_bot_reply")
    finally:
        db.close()


def test_category_b_message_hands_off_without_calling_gemini_answer() -> None:
    db = SessionLocal()
    tenant = _grip()
    phone = _test_phone()
    fake_gemini = FakeGeminiClient()

    try:
        result = process_inbound_message(db, tenant, phone, "necesito cambiar mi cita", gemini_client=fake_gemini)
        db.commit()

        assert result.status == ConversationStatus.WAITING_FOR_HUMAN
        assert result.awaiting_human is True
        assert fake_gemini.answer_calls == []  # never asked Gemini to freehand a category-B answer

        contact = db.query(Contact).filter(Contact.tenant_id == tenant.id, Contact.phone == phone).one()
        conversation = db.query(Conversation).filter(Conversation.contact_id == contact.id).one()
        assert conversation.handoff_priority == "MEDIUM"
        assert "APPOINTMENT_CHANGE" in conversation.handoff_reason

        _cleanup(db, contact.id)
        print("PASS: test_category_b_message_hands_off_without_calling_gemini_answer")
    finally:
        db.close()


def test_safety_risk_uses_tenants_configured_protocol_message() -> None:
    db = SessionLocal()
    tenant = _grip()
    phone = _test_phone()
    fake_gemini = FakeGeminiClient()

    try:
        result = process_inbound_message(db, tenant, phone, "ya no quiero seguir viviendo", gemini_client=fake_gemini)
        db.commit()

        assert result.status == ConversationStatus.WAITING_FOR_HUMAN
        assert "guardia de GRIP" in result.reply_text  # the tenant-configured message from scripts/seed_grip.py
        assert result.classification.priority.value == "CRITICAL"
        assert fake_gemini.answer_calls == []
        assert fake_gemini.classify_calls == []  # safety short-circuits before any Gemini call at all

        contact = db.query(Contact).filter(Contact.tenant_id == tenant.id, Contact.phone == phone).one()
        conversation = db.query(Conversation).filter(Conversation.contact_id == contact.id).one()
        assert conversation.handoff_priority == "CRITICAL"

        _cleanup(db, contact.id)
        print("PASS: test_safety_risk_uses_tenants_configured_protocol_message")
    finally:
        db.close()


def test_unclear_message_asks_one_clarifying_question() -> None:
    db = SessionLocal()
    tenant = _grip()
    phone = _test_phone()
    fake_gemini = FakeGeminiClient(classify_result="OTHER_UNCLEAR")

    try:
        result = process_inbound_message(db, tenant, phone, "buenas", gemini_client=fake_gemini)
        db.commit()

        assert result.status == ConversationStatus.WAITING_FOR_PATIENT
        assert result.awaiting_human is False
        assert len(fake_gemini.classify_calls) == 1

        contact = db.query(Contact).filter(Contact.tenant_id == tenant.id, Contact.phone == phone).one()
        _cleanup(db, contact.id)
        print("PASS: test_unclear_message_asks_one_clarifying_question")
    finally:
        db.close()


def test_bot_stays_silent_while_human_active() -> None:
    db = SessionLocal()
    tenant = _grip()
    phone = _test_phone()
    fake_gemini = FakeGeminiClient()

    try:
        # First message triggers a handoff (category B), then an agent claims it.
        process_inbound_message(db, tenant, phone, "necesito cancelar mi cita", gemini_client=fake_gemini)
        db.commit()

        contact = db.query(Contact).filter(Contact.tenant_id == tenant.id, Contact.phone == phone).one()
        conversation = db.query(Conversation).filter(Conversation.contact_id == contact.id).one()
        human_inbox.claim_conversation(db, conversation, agent_name="secretaria_1")
        db.commit()
        assert conversation.status == ConversationStatus.HUMAN_ACTIVE

        # Patient writes again while the agent has it — bot must stay silent.
        result = process_inbound_message(db, tenant, phone, "¿ya vieron mi mensaje?", gemini_client=fake_gemini)
        db.commit()

        assert result.reply_text is None
        assert result.status == ConversationStatus.HUMAN_ACTIVE
        assert fake_gemini.answer_calls == []

        _cleanup(db, contact.id)
        print("PASS: test_bot_stays_silent_while_human_active")
    finally:
        db.close()


def test_full_human_handoff_lifecycle_then_bot_resumes() -> None:
    db = SessionLocal()
    tenant = _grip()
    phone = _test_phone()
    fake_gemini = FakeGeminiClient(answer_text="Claro, aquí tienes los horarios.", answer_tools=["get_general_info"])

    try:
        process_inbound_message(db, tenant, phone, "necesito cambiar mi cita", gemini_client=fake_gemini)
        db.commit()

        contact = db.query(Contact).filter(Contact.tenant_id == tenant.id, Contact.phone == phone).one()
        conversation = db.query(Conversation).filter(Conversation.contact_id == contact.id).one()

        human_inbox.claim_conversation(db, conversation, agent_name="secretaria_1")
        db.commit()
        assert conversation.assigned_to == "secretaria_1"

        human_inbox.agent_reply(db, conversation, "Claro, ¿para qué fecha te gustaría reprogramar?")
        db.commit()

        human_inbox.resolve_conversation(db, conversation)
        db.commit()
        assert conversation.status == ConversationStatus.HUMAN_RESOLVED
        assert conversation.resolved_at is not None

        human_inbox.return_to_bot(db, conversation)
        db.commit()
        assert conversation.status == ConversationStatus.BOT_RESUMED
        assert conversation.assigned_to is None

        # Patient's next message should fold BOT_RESUMED -> BOT_ACTIVE and
        # get a real, tool-grounded answer again.
        result = process_inbound_message(db, tenant, phone, "cuentenme sobre GRIP", gemini_client=fake_gemini)
        db.commit()

        assert result.status == ConversationStatus.BOT_ACTIVE
        assert result.reply_text == "Claro, aquí tienes los horarios."

        _cleanup(db, contact.id)
        print("PASS: test_full_human_handoff_lifecycle_then_bot_resumes")
    finally:
        db.close()


def test_message_after_resolved_goes_back_to_human_not_bot() -> None:
    db = SessionLocal()
    tenant = _grip()
    phone = _test_phone()
    fake_gemini = FakeGeminiClient()

    try:
        process_inbound_message(db, tenant, phone, "necesito cambiar mi cita", gemini_client=fake_gemini)
        db.commit()

        contact = db.query(Contact).filter(Contact.tenant_id == tenant.id, Contact.phone == phone).one()
        conversation = db.query(Conversation).filter(Conversation.contact_id == contact.id).one()

        human_inbox.claim_conversation(db, conversation, agent_name="secretaria_1")
        human_inbox.resolve_conversation(db, conversation)
        db.commit()
        assert conversation.status == ConversationStatus.HUMAN_RESOLVED

        # No explicit "return to AI" happened — the patient just writes again.
        result = process_inbound_message(db, tenant, phone, "¿alguien me puede ayudar?", gemini_client=fake_gemini)
        db.commit()

        assert result.status == ConversationStatus.WAITING_FOR_HUMAN
        assert fake_gemini.answer_calls == []

        _cleanup(db, contact.id)
        print("PASS: test_message_after_resolved_goes_back_to_human_not_bot")
    finally:
        db.close()


if __name__ == "__main__":
    test_category_a_message_gets_a_grounded_bot_reply()
    test_category_b_message_hands_off_without_calling_gemini_answer()
    test_safety_risk_uses_tenants_configured_protocol_message()
    test_unclear_message_asks_one_clarifying_question()
    test_bot_stays_silent_while_human_active()
    test_full_human_handoff_lifecycle_then_bot_resumes()
    test_message_after_resolved_goes_back_to_human_not_bot()
    print("\nAll orchestrator + human inbox tests passed.")
