"""
GET /crm — the staff-authenticated screen for patients, appointments and
clinical notes. This is the real internal tool /dashboard deliberately is
NOT: /dashboard shows only counts and categories to anyone who finds the
URL, while everything here requires a login and enforces two roles:

  SECRETARY  patients + appointments (create/reschedule/mark status).
             Never sees clinical note content — not hidden by the UI
             alone, the API itself refuses it (see require_doctor).
  DOCTOR     everything a secretary sees, plus clinical notes and
             recommendations (read + write).

Accounts are created by scripts/create_staff.py, not a signup screen —
see that script's docstring for why.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.models.appointment import Appointment, AppointmentStatus
from app.models.clinical_note import ClinicalNoteType
from app.models.contact import Contact
from app.models.staff import Staff, StaffRole, StaffSession
from app.models.tenant import Tenant
from app.services import appointments as appointments_service
from app.services import auth, clinical_notes

router = APIRouter(prefix="/crm", tags=["crm"])


# --- auth dependencies -----------------------------------------------------


def get_current_staff(request: Request, db: Session = Depends(get_db)) -> Staff:
    token = request.cookies.get(auth.SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="No has iniciado sesión.")

    token_hash = auth.hash_session_token(token)
    session = db.query(StaffSession).filter(StaffSession.token_hash == token_hash).one_or_none()
    if session is None or session.expires_at < datetime.now(session.expires_at.tzinfo):
        raise HTTPException(status_code=401, detail="Tu sesión expiró. Inicia sesión de nuevo.")

    staff = db.query(Staff).filter(Staff.id == session.staff_id, Staff.is_active.is_(True)).one_or_none()
    if staff is None:
        raise HTTPException(status_code=401, detail="Cuenta inactiva.")
    return staff


def require_doctor(staff: Staff = Depends(get_current_staff)) -> Staff:
    """Gate for anything touching ClinicalNote. The frontend also hides
    these controls from a secretary, but that's a UX nicety, not the
    actual boundary — this dependency is."""
    if staff.role != StaffRole.DOCTOR:
        raise HTTPException(status_code=403, detail="Solo el personal médico puede ver o crear notas clínicas.")
    return staff


def _get_tenant(db: Session, staff: Staff) -> Tenant:
    return db.query(Tenant).filter(Tenant.id == staff.tenant_id).one()


def _get_contact_or_404(db: Session, staff: Staff, contact_id: str) -> Contact:
    """Every patient lookup is scoped to the logged-in staff member's own
    tenant — a GRIP secretary can never fetch another tenant's patient by
    guessing a UUID, even before there is a second tenant to worry about."""
    try:
        contact_uuid = uuid.UUID(contact_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Paciente no encontrado.")
    contact = db.query(Contact).filter(Contact.id == contact_uuid, Contact.tenant_id == staff.tenant_id).one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="Paciente no encontrado.")
    return contact


def _get_appointment_or_404(db: Session, staff: Staff, appointment_id: str) -> Appointment:
    try:
        appointment_uuid = uuid.UUID(appointment_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Cita no encontrada.")
    appointment = (
        db.query(Appointment)
        .filter(Appointment.id == appointment_uuid, Appointment.tenant_id == staff.tenant_id)
        .one_or_none()
    )
    if appointment is None:
        raise HTTPException(status_code=404, detail="Cita no encontrada.")
    return appointment


# --- schemas ----------------------------------------------------------------


class LoginRequest(BaseModel):
    tenant_slug: str = "grip"
    username: str
    password: str


class StaffOut(BaseModel):
    display_name: str
    role: str


class PatientSummaryOut(BaseModel):
    id: str
    name: str | None
    phone: str
    status: str
    last_interaction_at: str | None


class AppointmentOut(BaseModel):
    id: str
    therapist_name: str
    scheduled_at: str
    status: str
    reminder_sent_at: str | None
    reminder_response: str | None


class ClinicalNoteOut(BaseModel):
    id: str
    note_type: str
    author_name: str
    content: str
    created_at: str
    appointment_id: str | None


class PatientDetailOut(BaseModel):
    id: str
    name: str | None
    phone: str
    status: str
    appointments: list[AppointmentOut]
    notes: list[ClinicalNoteOut] | None  # None (not just []) when the caller is a secretary — omitted, not emptied


class CreateAppointmentRequest(BaseModel):
    therapist_name: str
    scheduled_at: datetime


class UpdateAppointmentRequest(BaseModel):
    therapist_name: str | None = None
    scheduled_at: datetime | None = None
    status: str | None = None


class CreateNoteRequest(BaseModel):
    note_type: str
    content: str
    appointment_id: str | None = None


# --- auth routes -------------------------------------------------------------


@router.post("/login", response_model=StaffOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> StaffOut:
    tenant = db.query(Tenant).filter(Tenant.slug == payload.tenant_slug).one_or_none()
    staff = (
        db.query(Staff)
        .filter(Staff.tenant_id == tenant.id, Staff.username == payload.username, Staff.is_active.is_(True))
        .one_or_none()
        if tenant is not None
        else None
    )
    # Same generic error whether the tenant, the username, or the password
    # was wrong — never reveal which one failed.
    if staff is None or not auth.verify_password(payload.password, staff.password_hash):
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")

    token = auth.new_session_token()
    session = StaffSession(
        staff_id=staff.id,
        token_hash=auth.hash_session_token(token),
        created_at=datetime.now(staff.created_at.tzinfo),
        expires_at=auth.new_session_expiry(),
    )
    db.add(session)
    db.commit()

    response.set_cookie(
        auth.SESSION_COOKIE_NAME,
        token,
        httponly=True,
        secure=get_settings().app_env == "production",
        samesite="lax",
        max_age=auth.SESSION_TTL_HOURS * 3600,
    )
    return StaffOut(display_name=staff.display_name, role=staff.role.value)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    token = request.cookies.get(auth.SESSION_COOKIE_NAME)
    if token:
        db.query(StaffSession).filter(StaffSession.token_hash == auth.hash_session_token(token)).delete()
        db.commit()
    response.delete_cookie(auth.SESSION_COOKIE_NAME)
    return {"ok": True}


@router.get("/me", response_model=StaffOut)
def me(staff: Staff = Depends(get_current_staff)) -> StaffOut:
    return StaffOut(display_name=staff.display_name, role=staff.role.value)


# --- patients ----------------------------------------------------------------


@router.get("/patients", response_model=list[PatientSummaryOut])
def list_patients(staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)) -> list[PatientSummaryOut]:
    contacts = (
        db.query(Contact)
        .filter(Contact.tenant_id == staff.tenant_id)
        .order_by(Contact.last_interaction_at.desc().nullslast())
        .all()
    )
    return [
        PatientSummaryOut(
            id=str(c.id),
            name=c.name or c.whatsapp_profile_name,
            phone=c.phone,
            status=c.status.value,
            last_interaction_at=c.last_interaction_at.isoformat() if c.last_interaction_at else None,
        )
        for c in contacts
    ]


@router.get("/patients/{contact_id}", response_model=PatientDetailOut)
def get_patient(contact_id: str, staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)) -> PatientDetailOut:
    contact = _get_contact_or_404(db, staff, contact_id)
    appointment_rows = (
        db.query(Appointment)
        .filter(Appointment.contact_id == contact.id)
        .order_by(Appointment.scheduled_at.desc())
        .all()
    )
    notes_out = None
    if staff.role == StaffRole.DOCTOR:
        tenant = _get_tenant(db, staff)
        notes_out = [
            ClinicalNoteOut(
                id=str(n.id),
                note_type=n.note_type.value,
                author_name=n.author_name,
                content=n.content,
                created_at=n.created_at.isoformat(),
                appointment_id=str(n.appointment_id) if n.appointment_id else None,
            )
            for n in clinical_notes.list_notes_for_contact(db, tenant, contact)
        ]

    return PatientDetailOut(
        id=str(contact.id),
        name=contact.name or contact.whatsapp_profile_name,
        phone=contact.phone,
        status=contact.status.value,
        appointments=[
            AppointmentOut(
                id=str(a.id),
                therapist_name=a.therapist_name,
                scheduled_at=a.scheduled_at.isoformat(),
                status=a.status.value,
                reminder_sent_at=a.reminder_sent_at.isoformat() if a.reminder_sent_at else None,
                reminder_response=a.reminder_response.value if a.reminder_response else None,
            )
            for a in appointment_rows
        ],
        notes=notes_out,
    )


# --- appointments (secretary + doctor) ---------------------------------------


@router.post("/patients/{contact_id}/appointments", response_model=AppointmentOut)
def create_appointment_route(
    contact_id: str,
    payload: CreateAppointmentRequest,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
) -> AppointmentOut:
    contact = _get_contact_or_404(db, staff, contact_id)
    tenant = _get_tenant(db, staff)
    appointment = appointments_service.create_appointment(
        db, tenant, contact, therapist_name=payload.therapist_name, scheduled_at=payload.scheduled_at
    )
    db.commit()
    return AppointmentOut(
        id=str(appointment.id),
        therapist_name=appointment.therapist_name,
        scheduled_at=appointment.scheduled_at.isoformat(),
        status=appointment.status.value,
        reminder_sent_at=None,
        reminder_response=None,
    )


@router.patch("/appointments/{appointment_id}", response_model=AppointmentOut)
def update_appointment_route(
    appointment_id: str,
    payload: UpdateAppointmentRequest,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
) -> AppointmentOut:
    appointment = _get_appointment_or_404(db, staff, appointment_id)

    if payload.therapist_name is not None:
        appointment.therapist_name = payload.therapist_name
    if payload.scheduled_at is not None:
        appointment.scheduled_at = payload.scheduled_at
    if payload.status is not None:
        try:
            appointment.status = AppointmentStatus(payload.status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Estado de cita inválido: {payload.status}")

    db.commit()
    return AppointmentOut(
        id=str(appointment.id),
        therapist_name=appointment.therapist_name,
        scheduled_at=appointment.scheduled_at.isoformat(),
        status=appointment.status.value,
        reminder_sent_at=appointment.reminder_sent_at.isoformat() if appointment.reminder_sent_at else None,
        reminder_response=appointment.reminder_response.value if appointment.reminder_response else None,
    )


# --- clinical notes (doctor only) --------------------------------------------


@router.post("/patients/{contact_id}/notes", response_model=ClinicalNoteOut)
def create_note_route(
    contact_id: str,
    payload: CreateNoteRequest,
    staff: Staff = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> ClinicalNoteOut:
    contact = _get_contact_or_404(db, staff, contact_id)
    tenant = _get_tenant(db, staff)
    try:
        note_type = ClinicalNoteType(payload.note_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Tipo de nota inválido: {payload.note_type}")

    appointment_uuid = uuid.UUID(payload.appointment_id) if payload.appointment_id else None
    note = clinical_notes.add_clinical_note(
        db,
        tenant,
        contact,
        author_name=staff.display_name,
        note_type=note_type,
        content=payload.content,
        appointment_id=appointment_uuid,
    )
    db.commit()
    return ClinicalNoteOut(
        id=str(note.id),
        note_type=note.note_type.value,
        author_name=note.author_name,
        content=note.content,
        created_at=note.created_at.isoformat(),
        appointment_id=str(note.appointment_id) if note.appointment_id else None,
    )