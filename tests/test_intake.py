"""
Tests for the new-patient intake (app/services/intake.py) wired through the
orchestrator: SeNaSa / Patronato / direct -> name -> concern -> secretary.
Gemini is never needed: the flow is deterministic (a fake that raises proves it).
 
Run with:  .venv/bin/python -m pytest tests/test_intake.py
"""
 
import uuid
 
import pytest
 
from app.api.routes import crm as crm_routes
from app.core.db import SessionLocal
from app.models.contact import Contact
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message
from app.models.staff import Staff, StaffRole
from app.models.tenant import Tenant
from app.services import auth, intake, tools
from app.services.orchestrator import process_inbound_message
from scripts.seed_grip import ND_CONFIG
 
SENASA_NOTE = (
    "Si eres afiliado a SeNaSa, tanto en el Régimen Contributivo como Subsidiado "
    "(No Contributivo), puedes acercarte a la sucursal de SeNaSa más cercana para "
    "conocer si aplicas al programa y recibir toda la información correspondiente."
)
 
 
class NoGemini:
    def answer(self, **kw):
        raise AssertionError("Gemini must not be used here")
 
    def classify_intent(self, **kw):
        raise AssertionError("Gemini must not classify intake answers")
 
 
def _tenant() -> Tenant:
    db = SessionLocal()
    real = db.query(Tenant).filter(Tenant.slug == "grip").one()
    db.close()
    return Tenant(id=real.id, name=real.name, slug=real.slug, language=real.language,
                  timezone=real.timezone, whatsapp_number=real.whatsapp_number, config=ND_CONFIG)
 
 
@pytest.fixture
def env():
    tenant = _tenant()
    db = SessionLocal()
    phone = f"+1555{uuid.uuid4().int % 10_000_000:07d}"
    yield db, tenant, phone
    contact = db.query(Contact).filter(Contact.phone == phone, Contact.tenant_id == tenant.id).one_or_none()
    if contact:
        convs = db.query(Conversation).filter(Conversation.contact_id == contact.id).all()
        for c in convs:
            db.query(Message).filter(Message.conversation_id == c.id).delete(synchronize_session=False)
            db.delete(c)
        db.delete(contact)
        db.commit()
    db.close()
 
 
def say(db, tenant, phone, text):
    r = process_inbound_message(db, tenant, phone, text, gemini_client=NoGemini())
    db.commit()
    return r
 
 
def conv(db, r):
    return db.query(Conversation).filter(Conversation.id == r.conversation_id).one()
 
 
def test_full_flow_senasa(env):
    db, tenant, phone = env
    r = say(db, tenant, phone, "Hola, quiero una cita para mi hijo")
    assert "SeNaSa" in r.reply_text and "Patronato" in r.reply_text and "1." in r.reply_text
    assert r.status == ConversationStatus.WAITING_FOR_PATIENT
 
    r = say(db, tenant, phone, "1")
    assert SENASA_NOTE in r.reply_text
    assert "nombre completo del paciente" in r.reply_text
 
    r = say(db, tenant, phone, "Mi hijo se llama Juan Pérez")
    assert "Juan Pérez" in r.reply_text
 
    r = say(db, tenant, phone, "No habla bien y le cuesta socializar")
    assert r.status == ConversationStatus.WAITING_FOR_HUMAN and r.awaiting_human
    c = conv(db, r)
    assert "Juan Pérez" in c.handoff_reason
    assert "SeNaSa" in c.handoff_reason
    assert "No habla bien" in c.handoff_reason
    assert c.handoff_priority == "MEDIUM"
    assert c.intake_data["step"] == intake.STEP_DONE
 
    # After the handoff the bot is silent.
    r = say(db, tenant, phone, "gracias")
    assert r.reply_text is None
 
 
def test_payer_in_first_message_skips_the_question(env):
    db, tenant, phone = env
    r = say(db, tenant, phone, "Quiero una cita, por mi cuenta")
    assert "nombre completo" in r.reply_text
    assert SENASA_NOTE not in r.reply_text
    assert conv(db, r).intake_data["payer"] == "DIRECT"
 
 
def test_patronato_by_text(env):
    db, tenant, phone = env
    say(db, tenant, phone, "necesito terapia")
    r = say(db, tenant, phone, "es por el patronato de sordomudos")
    assert "nombre completo" in r.reply_text
    assert conv(db, r).intake_data["payer"] == "PATRONATO"
 
 
def test_unrecognized_payer_repeats_once_then_continues(env):
    db, tenant, phone = env
    say(db, tenant, phone, "quiero terapia")
    r = say(db, tenant, phone, "no se")
    assert "No logré identificar" in r.reply_text
    r = say(db, tenant, phone, "mmm")
    assert "nombre completo" in r.reply_text
    assert conv(db, r).intake_data["payer"] == "UNSPECIFIED"
 
 
def test_name_reasked_when_empty(env):
    db, tenant, phone = env
    say(db, tenant, phone, "quiero terapia")
    say(db, tenant, phone, "3")  # DIRECT is option 3
    r = say(db, tenant, phone, "...")
    assert "nombre completo" in r.reply_text
    assert conv(db, r).intake_data["step"] == intake.STEP_NAME
 
 
def test_safety_message_interrupts_the_intake(env):
    db, tenant, phone = env
    say(db, tenant, phone, "quiero terapia")
    r = say(db, tenant, phone, "a veces no quiero vivir")
    c = conv(db, r)
    assert r.status == ConversationStatus.WAITING_FOR_HUMAN
    assert c.intake_data["step"] == intake.STEP_ABANDONED
 
 
def test_human_request_interrupts_the_intake(env):
    db, tenant, phone = env
    say(db, tenant, phone, "quiero terapia")
    r = say(db, tenant, phone, "quiero hablar con una persona")
    assert r.status == ConversationStatus.WAITING_FOR_HUMAN
    assert conv(db, r).intake_data["step"] == intake.STEP_ABANDONED
 
 
def test_question_during_payer_step_leaves_the_intake(env):
    db, tenant, phone = env
    say(db, tenant, phone, "quiero terapia")
    c = db.query(Conversation).join(Contact).filter(Contact.phone == phone).one()
    # a question is not a payer answer: intake is abandoned, normal pipeline runs
    process_inbound_message(db, tenant, phone, "¿dónde quedan?", gemini_client=None)
    db.commit()
    db.refresh(c)
    assert c.intake_data["step"] == intake.STEP_ABANDONED
 
 
def test_expired_intake_is_not_continued(env):
    db, tenant, phone = env
    r = say(db, tenant, phone, "quiero terapia")
    c = conv(db, r)
    old = dict(c.intake_data)
    old["updated_at"] = "2020-01-01T00:00:00+00:00"
    c.intake_data = old
    db.commit()
    assert not intake.is_active(c)
 
 
def test_booking_guidance_starts_intake(env):
    db, tenant, phone = env
    r = say(db, tenant, phone, "¿cómo agendo?")
    assert "SeNaSa" in r.reply_text
 
 
def test_tenant_without_intake_keeps_old_behavior(env):
    db, tenant, phone = env
    plain = Tenant(id=tenant.id, name=tenant.name, slug=tenant.slug, language="es",
                   timezone="UTC", whatsapp_number=tenant.whatsapp_number, config={})
    r = process_inbound_message(db, plain, phone, "quiero una cita", gemini_client=NoGemini())
    db.commit()
    assert r.status == ConversationStatus.WAITING_FOR_HUMAN  # generic category-B handoff
    assert conv(db, r).intake_data is None
 
 
def test_services_are_configured_and_tool_mentions_registration():
    tenant = _tenant()
    out = tools.get_services(tenant)
    names = [s["name"] for s in out["services"]]
    assert names == ["Evaluación y diagnóstico", "Atención psicológica", "Terapias", "Acompañamiento familiar"]
    assert "registration_note" in out
    assert "SeNaSa Integra" in out["services"][0]["description"]
 
 
def test_services_keywords_route_to_services():
    from app.services.intent_rules import deterministic_candidates
    assert [m.intent.value for m in deterministic_candidates("¿qué terapias tienen?")] == ["SERVICES"]
 
 
def test_crm_case_exposes_intake(env):
    db, tenant, phone = env
    for t in ("quiero terapia", "1", "Ana Gómez", "ansiedad"):
        r = say(db, tenant, phone, t)
    real = db.query(Tenant).filter(Tenant.slug == "grip").one()
    staff = Staff(tenant_id=real.id, username=f"t_{uuid.uuid4().hex[:8]}", display_name="Sec",
                  role=StaffRole.SECRETARY, password_hash=auth.hash_password("clave-correcta-123"), is_active=True)
    db.add(staff)
    db.commit()
    try:
        detail = crm_routes.get_case(r.conversation_id, staff, db)
        assert detail.intake.patient_name == "Ana Gómez"
        assert detail.intake.payer_label.startswith("SeNaSa")
        assert detail.intake.concern == "ansiedad"
    finally:
        db.delete(staff)
        db.commit()