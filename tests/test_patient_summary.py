"""
Tests for the doctor-initiated "Resumen con IA" (POST /crm/patients/{id}/summary
and app/services/patient_summary.py). Gemini is always faked: this sandbox
cannot reach Google, and these tests are about wiring, access control and what
is (and is not) sent to the model.

Run with:
    .venv/bin/python -m pytest tests/test_patient_summary.py
"""

import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.routes import crm as crm_routes
from app.core.db import SessionLocal
from app.models.appointment import Appointment
from app.models.clinical_note import ClinicalNote, ClinicalNoteType
from app.models.contact import Contact
from app.models.staff import Staff, StaffRole
from app.models.tenant import Tenant
from app.services import appointments as appointments_service
from app.services import auth, patient_summary

PHONE_SECRET_NAME = "Nombre Secreto Paciente"


class FakeModels:
    def __init__(self, text="Lo esencial: resumen de prueba.", error=None):
        self.text = text
        self.error = error
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return types.SimpleNamespace(text=self.text)


class FakeClient:
    def __init__(self, **kw):
        self.models = FakeModels(**kw)


def _grip() -> Tenant:
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == "grip").one()
    db.close()
    return tenant


def _phone() -> str:
    return f"+1555{uuid.uuid4().int % 10_000_000:07d}"


def _staff(db, tenant, role) -> Staff:
    staff = Staff(
        tenant_id=tenant.id,
        username=f"test_{uuid.uuid4().hex[:10]}",
        display_name="Dra. Prueba",
        role=role,
        password_hash=auth.hash_password("clave-correcta-123"),
        is_active=True,
    )
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff


def _contact(db, tenant, *, with_notes=True) -> Contact:
    contact = Contact(tenant_id=tenant.id, phone=_phone(), name=PHONE_SECRET_NAME)
    db.add(contact)
    db.flush()
    appointments_service.create_appointment(
        db, tenant, contact, therapist_name="Terapeuta X",
        scheduled_at=datetime.now(timezone.utc) - timedelta(days=3),
    )
    if with_notes:
        db.add(ClinicalNote(
            tenant_id=tenant.id, contact_id=contact.id, author_name="Lucía Torres",
            note_type=ClinicalNoteType.NOTE, content="Dificultad de atención en clase.",
        ))
        db.add(ClinicalNote(
            tenant_id=tenant.id, contact_id=contact.id, author_name="Lucía Torres",
            note_type=ClinicalNoteType.RECOMMENDATION, content="Usar temporizador visual.",
        ))
    db.commit()
    db.refresh(contact)
    return contact


def _cleanup(db, staff, contact):
    db.query(ClinicalNote).filter(ClinicalNote.contact_id == contact.id).delete(synchronize_session=False)
    db.query(Appointment).filter(Appointment.contact_id == contact.id).delete(synchronize_session=False)
    db.query(Contact).filter(Contact.id == contact.id).delete(synchronize_session=False)
    db.query(Staff).filter(Staff.id == staff.id).delete(synchronize_session=False)
    db.commit()


@pytest.fixture
def env():
    tenant = _grip()
    db = SessionLocal()
    staff = _staff(db, tenant, StaffRole.DOCTOR)
    contact = _contact(db, tenant)
    yield db, tenant, staff, contact
    _cleanup(db, staff, contact)
    db.close()


@pytest.fixture
def fake_gemini(monkeypatch):
    """Replaces genai.Client inside patient_summary and gives it an API key."""
    holder = {}

    def factory(**kw):
        holder["client"] = FakeClient(**holder.get("kw", {}))
        return holder["client"]

    monkeypatch.setattr(patient_summary.genai, "Client", factory)
    monkeypatch.setattr(
        patient_summary, "get_settings",
        lambda: types.SimpleNamespace(gemini_api_key="fake-key", gemini_model="fake-model"),
    )
    return holder


def test_secretary_cannot_use_the_summary_route():
    tenant = _grip()
    db = SessionLocal()
    secretary = _staff(db, tenant, StaffRole.SECRETARY)
    try:
        with pytest.raises(HTTPException) as exc:
            crm_routes.require_doctor(secretary)
        assert exc.value.status_code == 403
    finally:
        db.query(Staff).filter(Staff.id == secretary.id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_doctor_gets_a_summary(env, fake_gemini):
    db, _tenant, staff, contact = env
    result = crm_routes.patient_summary_route(str(contact.id), staff, db)
    assert result.summary == "Lo esencial: resumen de prueba."
    assert result.notes_count == 2
    call = fake_gemini["client"].models.calls[0]
    assert call["model"] == "fake-model"
    assert "No diagnostiques" in call["config"].system_instruction


def test_prompt_sends_notes_but_not_name_or_phone(env, fake_gemini):
    db, _tenant, staff, contact = env
    crm_routes.patient_summary_route(str(contact.id), staff, db)
    prompt = fake_gemini["client"].models.calls[0]["contents"]
    assert "Dificultad de atención en clase." in prompt
    assert "Usar temporizador visual." in prompt
    assert "Terapeuta X" in prompt
    assert PHONE_SECRET_NAME not in prompt
    assert contact.phone not in prompt


def test_no_notes_means_no_gemini_call(fake_gemini):
    tenant = _grip()
    db = SessionLocal()
    staff = _staff(db, tenant, StaffRole.DOCTOR)
    contact = _contact(db, tenant, with_notes=False)
    try:
        result = crm_routes.patient_summary_route(str(contact.id), staff, db)
        assert result.summary == patient_summary.NO_NOTES_MESSAGE
        assert result.notes_count == 0
        assert "client" not in fake_gemini
    finally:
        _cleanup(db, staff, contact)
        db.close()


def test_gemini_failure_is_a_502(env, fake_gemini):
    db, _tenant, staff, contact = env
    fake_gemini["kw"] = {"error": RuntimeError("sin red")}
    with pytest.raises(HTTPException) as exc:
        crm_routes.patient_summary_route(str(contact.id), staff, db)
    assert exc.value.status_code == 502


def test_empty_gemini_answer_is_a_502(env, fake_gemini):
    db, _tenant, staff, contact = env
    fake_gemini["kw"] = {"text": "   "}
    with pytest.raises(HTTPException) as exc:
        crm_routes.patient_summary_route(str(contact.id), staff, db)
    assert exc.value.status_code == 502


def test_missing_api_key_is_a_503(env, monkeypatch):
    db, _tenant, staff, contact = env
    monkeypatch.setattr(
        patient_summary, "get_settings",
        lambda: types.SimpleNamespace(gemini_api_key="", gemini_model="m"),
    )
    with pytest.raises(HTTPException) as exc:
        crm_routes.patient_summary_route(str(contact.id), staff, db)
    assert exc.value.status_code == 503


def test_other_tenants_patient_is_404(env, fake_gemini):
    db, _tenant, staff, _contact_ = env
    other = Tenant(name="Otro", slug=f"otro-{uuid.uuid4().hex[:6]}", whatsapp_number=f"whatsapp:+1{uuid.uuid4().int % 10**10}", timezone="UTC", language="es")
    db.add(other)
    db.flush()
    foreign = Contact(tenant_id=other.id, phone=_phone())
    db.add(foreign)
    db.commit()
    try:
        with pytest.raises(HTTPException) as exc:
            crm_routes.patient_summary_route(str(foreign.id), staff, db)
        assert exc.value.status_code == 404
    finally:
        db.query(Contact).filter(Contact.id == foreign.id).delete(synchronize_session=False)
        db.query(Tenant).filter(Tenant.id == other.id).delete(synchronize_session=False)
        db.commit()


def test_patient_list_still_works_next_to_the_summary_route(env):
    # Regression: the summary response model once shadowed the list's
    # PatientSummaryOut and broke GET /crm/patients.
    db, _tenant, staff, contact = env
    rows = crm_routes.list_patients(staff, db)
    assert str(contact.id) in {r.id for r in rows}