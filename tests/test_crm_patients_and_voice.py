"""
Tests for:
  - PATCH /crm/patients/{id} (editing a patient's name) — added so
    build_reminder_message's personalization ("Hola {name}, ...") has a
    name to use, since nothing previously let staff set Contact.name.
  - GET /crm/.../voice-reminder-preview — the voice-reminder prototype
    endpoint. voice_reminders.synthesize_reminder_voice is monkeypatched
    here (this is a route-wiring/auth test, not a re-test of the TTS
    pipeline itself — that's tests/test_voice_reminders.py).

Run with:
    .venv/bin/python -m tests.test_crm_patients_and_voice
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app.api.routes import crm as crm_routes
from app.core.db import SessionLocal
from app.models.appointment import Appointment
from app.models.contact import Contact
from app.models.staff import Staff, StaffRole
from app.models.tenant import Tenant
from app.services import appointments as appointments_service
from app.services import auth


def _grip() -> Tenant:
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == "grip").one()
    db.close()
    return tenant


def _test_phone() -> str:
    return f"+1555{uuid.uuid4().int % 10_000_000:07d}"


def _make_staff(db, tenant, role: StaffRole) -> Staff:
    staff = Staff(
        tenant_id=tenant.id,
        username=f"test_{uuid.uuid4().hex[:10]}",
        display_name="Personal de Prueba",
        role=role,
        password_hash=auth.hash_password("clave-correcta-123"),
        is_active=True,
    )
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff


def _make_contact_with_appointment(db, tenant) -> tuple[Contact, Appointment]:
    contact = Contact(tenant_id=tenant.id, phone=_test_phone())
    db.add(contact)
    db.flush()
    appointment = appointments_service.create_appointment(
        db, tenant, contact, therapist_name="Terapeuta de Prueba", scheduled_at=datetime.now(timezone.utc) + timedelta(hours=20)
    )
    db.commit()
    db.refresh(contact)
    db.refresh(appointment)
    return contact, appointment


def _cleanup(db, staff: Staff | None, contact: Contact | None) -> None:
    if contact is not None:
        db.query(Appointment).filter(Appointment.contact_id == contact.id).delete(synchronize_session=False)
        db.query(Contact).filter(Contact.id == contact.id).delete(synchronize_session=False)
    if staff is not None:
        db.query(Staff).filter(Staff.id == staff.id).delete(synchronize_session=False)
    db.commit()


def test_update_patient_route_sets_and_clears_name():
    tenant = _grip()
    db = SessionLocal()
    staff = _make_staff(db, tenant, StaffRole.SECRETARY)
    contact, _appointment = _make_contact_with_appointment(db, tenant)
    try:
        result = crm_routes.update_patient_route(
            str(contact.id), crm_routes.UpdatePatientRequest(name="Juana Pérez"), staff, db
        )
        assert result.name == "Juana Pérez"
        db.refresh(contact)
        assert contact.name == "Juana Pérez"

        # Reused by build_reminder_message ("Hola {name}, ...") — confirm
        # the whole point of this endpoint actually works end to end.
        message = appointments_service.build_reminder_message(_appointment, contact)
        assert "Juana Pérez" in message

        # Blank/whitespace clears the name back to None rather than storing "".
        result2 = crm_routes.update_patient_route(str(contact.id), crm_routes.UpdatePatientRequest(name="   "), staff, db)
        assert result2.name is None
        db.refresh(contact)
        assert contact.name is None
    finally:
        _cleanup(db, staff, contact)
        db.close()


def test_update_patient_route_scoped_to_staff_tenant():
    """_get_contact_or_404 already enforces this for every other patient
    endpoint — this just confirms the new route didn't bypass it."""
    tenant = _grip()
    db = SessionLocal()
    staff = _make_staff(db, tenant, StaffRole.SECRETARY)
    try:
        try:
            crm_routes.update_patient_route(str(uuid.uuid4()), crm_routes.UpdatePatientRequest(name="X"), staff, db)
            assert False, "expected HTTPException for a nonexistent contact"
        except HTTPException as exc:
            assert exc.status_code == 404
    finally:
        _cleanup(db, staff, None)
        db.close()


def test_voice_reminder_preview_returns_ogg_audio_and_uses_the_real_message_text(monkeypatch):
    tenant = _grip()
    db = SessionLocal()
    staff = _make_staff(db, tenant, StaffRole.SECRETARY)  # not clinical data — either role can use it
    contact, appointment = _make_contact_with_appointment(db, tenant)
    contact.name = "Juana Pérez"
    db.commit()

    captured_text = {}

    def fake_synthesize(text, *, client=None):
        captured_text["value"] = text
        return b"OggS-fake-audio-bytes"

    monkeypatch.setattr(crm_routes.voice_reminders, "synthesize_reminder_voice", fake_synthesize)

    try:
        response = crm_routes.voice_reminder_preview(str(contact.id), str(appointment.id), staff, db)
        assert response.media_type == "audio/ogg"
        assert response.body == b"OggS-fake-audio-bytes"
        # Confirms the endpoint feeds it the SAME text the real text
        # reminder would send (appointments_service.build_reminder_message),
        # not some separate/independent copy that could drift out of sync.
        expected_text = appointments_service.build_reminder_message(appointment, contact)
        assert captured_text["value"] == expected_text
        assert "Juana Pérez" in captured_text["value"]
    finally:
        _cleanup(db, staff, contact)
        db.close()


def test_voice_reminder_preview_404s_on_mismatched_patient_and_appointment(monkeypatch):
    """An appointment ID that belongs to a DIFFERENT patient than the one
    in the URL must 404, not silently preview the wrong patient's cita."""
    tenant = _grip()
    db = SessionLocal()
    staff = _make_staff(db, tenant, StaffRole.SECRETARY)
    contact_a, appointment_a = _make_contact_with_appointment(db, tenant)
    contact_b, _appointment_b = _make_contact_with_appointment(db, tenant)

    def fake_synthesize(text, *, client=None):
        raise AssertionError("should not be reached for a mismatched patient/appointment pair")

    monkeypatch.setattr(crm_routes.voice_reminders, "synthesize_reminder_voice", fake_synthesize)

    try:
        try:
            crm_routes.voice_reminder_preview(str(contact_b.id), str(appointment_a.id), staff, db)
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 404
    finally:
        _cleanup(db, staff, contact_a)
        _cleanup(db, None, contact_b)
        db.close()


def test_voice_reminder_preview_returns_502_on_synthesis_failure(monkeypatch):
    tenant = _grip()
    db = SessionLocal()
    staff = _make_staff(db, tenant, StaffRole.DOCTOR)
    contact, appointment = _make_contact_with_appointment(db, tenant)

    def failing_synthesize(text, *, client=None):
        raise crm_routes.voice_reminders.VoiceSynthesisError("no network in this sandbox")

    monkeypatch.setattr(crm_routes.voice_reminders, "synthesize_reminder_voice", failing_synthesize)

    try:
        try:
            crm_routes.voice_reminder_preview(str(contact.id), str(appointment.id), staff, db)
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 502
    finally:
        _cleanup(db, staff, contact)
        db.close()