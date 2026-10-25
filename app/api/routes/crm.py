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

import hmac
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.models.appointment import Appointment, AppointmentStatus
from app.models.clinical_note import ClinicalNoteType
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.staff import Staff, StaffRole, StaffSession
from app.models.tenant import Tenant
from app.services import agenda, payments
from app.services import appointments as appointments_service
from app.models.billing import Payment
from app.services.conversation_state import InvalidTransitionError
from app.services import agent_reply, auth, clinical_notes, patient_summary, voice_reminders

from app.api.routes.crm_deps import (  # noqa: E402  (shared with crm_billing)
    _get_appointment_or_404,
    _get_contact_or_404,
    _get_tenant,
    get_current_staff,
)

router = APIRouter(prefix="/crm", tags=["crm"])

from app.api.routes import crm_billing  # noqa: E402

router.include_router(crm_billing.router)


# --- auth dependencies -----------------------------------------------------


def require_doctor(staff: Staff = Depends(get_current_staff)) -> Staff:
    """Gate for anything touching ClinicalNote. The frontend also hides
    these controls from a secretary, but that's a UX nicety, not the
    actual boundary — this dependency is."""
    if staff.role != StaffRole.DOCTOR:
        raise HTTPException(status_code=403, detail="Solo el personal médico puede ver o crear notas clínicas.")
    return staff


def _check_bootstrap_secret(provided: str) -> None:
    """Guards POST /crm/bootstrap-staff — see Settings.staff_bootstrap_secret.
    404 (not 401/403) both when the feature is off and when the secret is
    wrong, so the endpoint doesn't even reveal it exists to someone probing
    without the secret. hmac.compare_digest avoids leaking the secret's
    length/prefix through response-timing differences."""
    configured = get_settings().staff_bootstrap_secret
    if not configured or not hmac.compare_digest(provided, configured):
        raise HTTPException(status_code=404, detail="Not Found")


def _get_case_or_404(db: Session, staff: Staff, conversation_id: str) -> Conversation:
    try:
        conversation_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_uuid, Conversation.tenant_id == staff.tenant_id)
        .one_or_none()
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return conversation


# --- schemas ----------------------------------------------------------------


class LoginRequest(BaseModel):
    tenant_slug: str = "grip"
    username: str
    password: str


class StaffOut(BaseModel):
    display_name: str
    role: str


class BootstrapStaffRequest(BaseModel):
    secret: str
    tenant_slug: str = "grip"
    username: str
    display_name: str
    role: str
    password: str


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
    reminder_48h_sent_at: str | None = None
    reminder_24h_sent_at: str | None = None
    duration_minutes: int = 60
    payment_status: str | None = None


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


class UpdatePatientRequest(BaseModel):
    # Just the name for now — it's the one field the voice-reminder
    # prototype needs (build_reminder_message already personalizes with
    # contact.name when it's set) and it isn't clinical data, so either
    # role can set it. WhatsApp only gives us whatsapp_profile_name
    # automatically, which is often a nickname or not set at all.
    name: str | None = None


class CreateNoteRequest(BaseModel):
    note_type: str
    content: str
    appointment_id: str | None = None


class CaseSummaryOut(BaseModel):
    id: str
    contact_id: str
    contact_name: str | None
    contact_phone: str
    status: str
    handoff_reason: str | None
    handoff_priority: str | None
    handoff_triggered_at: str | None
    claimed_at: str | None
    assigned_to: str | None
    last_message_at: str | None


class CaseMessageOut(BaseModel):
    id: str
    direction: str
    sender: str
    body: str | None
    intent: str | None
    created_at: str


class CaseDetailOut(CaseSummaryOut):
    messages: list[CaseMessageOut]
    payment_appointment_id: str | None = None  # appointment whose payment is awaiting a decision
    can_reply_freely: bool = True  # False once the patient's 24h WhatsApp window has closed


class CaseReplyRequest(BaseModel):
    text: str


class CaseResolveRequest(BaseModel):
    action: str  # "return_to_bot" | "close"


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


# --- one-time staff bootstrap (no login required — see _check_bootstrap_secret) ---


@router.post("/bootstrap-staff", response_model=StaffOut)
def bootstrap_staff(payload: BootstrapStaffRequest, db: Session = Depends(get_db)) -> StaffOut:
    _check_bootstrap_secret(payload.secret)

    tenant = db.query(Tenant).filter(Tenant.slug == payload.tenant_slug).one_or_none()
    if tenant is None:
        raise HTTPException(status_code=400, detail=f"No existe un tenant con slug '{payload.tenant_slug}'.")

    try:
        role = StaffRole(payload.role.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail="El rol debe ser 'secretary' o 'doctor'.")

    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 8 caracteres.")

    staff = (
        db.query(Staff)
        .filter(Staff.tenant_id == tenant.id, Staff.username == payload.username)
        .one_or_none()
    )
    if staff is None:
        staff = Staff(tenant_id=tenant.id, username=payload.username, display_name=payload.display_name, role=role)
        db.add(staff)
    else:
        # Same "safe to re-run" behavior as scripts/create_staff.py: updates
        # and reactivates an existing account rather than failing.
        staff.display_name = payload.display_name
        staff.role = role
        staff.is_active = True
    staff.password_hash = auth.hash_password(payload.password)
    db.commit()
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

    pay_status = {
        p.appointment_id: p.status
        for p in db.query(Payment).filter(Payment.appointment_id.in_([a.id for a in appointment_rows] or [uuid.UUID(int=0)]))
    }
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
                reminder_48h_sent_at=a.reminder_48h_sent_at.isoformat() if a.reminder_48h_sent_at else None,
                reminder_24h_sent_at=a.reminder_24h_sent_at.isoformat() if a.reminder_24h_sent_at else None,
                duration_minutes=a.duration_minutes or 60,
                payment_status=pay_status.get(a.id),
            )
            for a in appointment_rows
        ],
        notes=notes_out,
    )


@router.patch("/patients/{contact_id}", response_model=PatientDetailOut)
def update_patient_route(
    contact_id: str,
    payload: UpdatePatientRequest,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
) -> PatientDetailOut:
    contact = _get_contact_or_404(db, staff, contact_id)
    if payload.name is not None:
        contact.name = payload.name.strip() or None
    db.commit()
    return get_patient(contact_id, staff, db)


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
    start = _aware(payload.scheduled_at)
    therapist = payload.therapist_name.strip()
    if not therapist:
        raise HTTPException(status_code=400, detail="Elige un terapeuta.")
    _check_slot(db, tenant, therapist=therapist, contact_id=contact.id, start=start)
    appointment = appointments_service.create_appointment(
        db, tenant, contact, therapist_name=therapist, scheduled_at=start
    )
    db.commit()
    return AppointmentOut(
        id=str(appointment.id),
        therapist_name=appointment.therapist_name,
        scheduled_at=appointment.scheduled_at.isoformat(),
        status=appointment.status.value,
        reminder_sent_at=None,
        reminder_response=None,
        duration_minutes=appointment.duration_minutes or 60,
    )


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _check_slot(db: Session, tenant: Tenant, *, therapist: str, contact_id, start: datetime, exclude_id=None) -> None:
    """Opening hours + no double-booking (same therapist, or same patient)."""
    error = agenda.slot_error(tenant, start)
    if error:
        raise HTTPException(status_code=400, detail=error)
    conflicts = agenda.find_conflicts(
        db, tenant, therapist_name=therapist, contact_id=contact_id, start=start, exclude_id=exclude_id
    )
    if conflicts:
        raise HTTPException(status_code=409, detail=agenda.conflict_message(conflicts))


@router.patch("/appointments/{appointment_id}", response_model=AppointmentOut)
def update_appointment_route(
    appointment_id: str,
    payload: UpdateAppointmentRequest,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
) -> AppointmentOut:
    appointment = _get_appointment_or_404(db, staff, appointment_id)

    new_status = appointment.status
    if payload.status is not None:
        try:
            new_status = AppointmentStatus(payload.status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Estado de cita inválido: {payload.status}")

    new_therapist = (payload.therapist_name or appointment.therapist_name).strip()
    new_start = _aware(payload.scheduled_at) if payload.scheduled_at is not None else appointment.scheduled_at
    time_or_person_changed = new_therapist != appointment.therapist_name or new_start != appointment.scheduled_at
    reopened = new_status in agenda.OCCUPYING and appointment.status not in agenda.OCCUPYING
    # Opening hours + double-booking are re-checked only when the slot or the
    # therapist changes, or a cancelled/finished appointment is put back on the books.
    if new_status in agenda.OCCUPYING and (time_or_person_changed or reopened):
        _check_slot(
            db, _get_tenant(db, staff), therapist=new_therapist, contact_id=appointment.contact_id,
            start=new_start, exclude_id=appointment.id,
        )
    if new_start != appointment.scheduled_at:
        # A new date is a new appointment as far as reminders go: clear what
        # was sent for the old one, and a "reschedule requested" appointment
        # that has just been moved is no longer waiting for anyone.
        appointment.reminder_sent_at = None
        appointment.reminder_48h_sent_at = None
        appointment.reminder_24h_sent_at = None
        appointment.reminder_response = None
        if new_status == AppointmentStatus.RESCHEDULE_REQUESTED:
            new_status = AppointmentStatus.SCHEDULED
    appointment.therapist_name = new_therapist
    appointment.scheduled_at = new_start
    appointment.status = new_status

    db.commit()
    return AppointmentOut(
        id=str(appointment.id),
        therapist_name=appointment.therapist_name,
        scheduled_at=appointment.scheduled_at.isoformat(),
        status=appointment.status.value,
        reminder_sent_at=appointment.reminder_sent_at.isoformat() if appointment.reminder_sent_at else None,
        reminder_response=appointment.reminder_response.value if appointment.reminder_response else None,
    )


# --- manual reminder (secretary + doctor) ------------------------------------


class SendReminderOut(BaseModel):
    message: str
    sent_at: str


@router.post("/patients/{contact_id}/appointments/{appointment_id}/send-reminder", response_model=SendReminderOut)
def send_reminder_route(
    contact_id: str,
    appointment_id: str,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
) -> SendReminderOut:
    """Sends the same reminder the automatic job sends (summary from the
    notes + payment question) right now, on demand. Works for either role:
    the summary is generated server-side and the secretary never receives
    the notes themselves. Does not touch the 48h/24h flags, so it never
    blocks the automatic reminders."""
    contact = _get_contact_or_404(db, staff, contact_id)
    appointment = _get_appointment_or_404(db, staff, appointment_id)
    if appointment.contact_id != contact.id:
        raise HTTPException(status_code=404, detail="Cita no encontrada.")
    if appointment.status not in (AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED):
        raise HTTPException(status_code=400, detail="Solo se puede enviar recordatorio de citas programadas o confirmadas.")
    tenant = _get_tenant(db, staff)
    try:
        body = appointments_service.send_reminder(db, tenant, appointment, contact, kind="manual", use_notes_summary=True)
    except appointments_service.ReminderDeliveryError:
        db.rollback()
        raise HTTPException(
            status_code=502,
            detail="No se pudo enviar el recordatorio por WhatsApp. Si el paciente no ha escrito en las últimas 24 horas, "
            "hace falta la plantilla aprobada de Meta (META_REMINDER_TEMPLATE).",
        )
    db.commit()
    return SendReminderOut(message=body, sent_at=appointment.reminder_sent_at.isoformat())


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


class AiSummaryOut(BaseModel):
    summary: str
    generated_at: str
    notes_count: int


@router.post("/patients/{contact_id}/summary", response_model=AiSummaryOut)
def patient_summary_route(
    contact_id: str,
    staff: Staff = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> AiSummaryOut:
    """On-demand AI summary of a patient's notes. Doctor-only, never
    automatic, nothing is stored. See app/services/patient_summary.py for
    what is (and is not) sent to the model."""
    contact = _get_contact_or_404(db, staff, contact_id)
    tenant = _get_tenant(db, staff)
    try:
        result = patient_summary.summarize_patient(db, tenant, contact)
    except patient_summary.SummaryUnavailableError:
        raise HTTPException(status_code=503, detail="El resumen con IA no está configurado (falta GEMINI_API_KEY).")
    except patient_summary.SummaryGenerationError:
        raise HTTPException(status_code=502, detail="No se pudo generar el resumen ahora mismo. Intenta de nuevo en un momento.")
    return AiSummaryOut(
        summary=result.summary,
        generated_at=result.generated_at.isoformat(),
        notes_count=result.notes_count,
    )


# --- voice reminder prototype (secretary + doctor) ---------------------------
#
# PROTOTYPE ONLY — lets staff listen to what a reminder would sound like as
# a WhatsApp voice note. Does not send anything to the patient and does not
# touch Appointment.reminder_sent_at; appointments_service.send_reminder
# (the real pipeline) is completely untouched by this. Needs GEMINI_API_KEY
# configured and outbound network access to Google's API to actually work.


@router.get("/patients/{contact_id}/appointments/{appointment_id}/voice-reminder-preview")
def voice_reminder_preview(
    contact_id: str,
    appointment_id: str,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
) -> Response:
    contact = _get_contact_or_404(db, staff, contact_id)
    appointment = _get_appointment_or_404(db, staff, appointment_id)
    if appointment.contact_id != contact.id:
        raise HTTPException(status_code=404, detail="Cita no encontrada.")

    text = appointments_service.build_reminder_message(appointment, contact)
    try:
        audio = voice_reminders.synthesize_reminder_voice(text)
    except voice_reminders.VoiceSynthesisError as exc:
        # 502: the failure is Gemini's/ffmpeg's, not something wrong with
        # this request — matters for telling a real bug apart from "this
        # environment can't reach Google's API" while prototyping.
        raise HTTPException(status_code=502, detail=str(exc))
    return Response(content=audio, media_type="audio/ogg")


# --- escalated cases (secretary + doctor) -----------------------------------
#
# The dashboard (GET /dashboard) only ever shows aggregate counts of
# handoffs/safety alerts — on purpose, per app.services.analytics's module
# docstring, so that public-ish metrics view never carries message
# content. This is the actual case log behind those counts: every
# conversation that was ever escalated to a human (handoff_reason set),
# open or already resolved, with its full transcript — staff-authenticated,
# same as the rest of this file. Visible to either role for now: a
# secretary legitimately needs this for payment/appointment-change/
# human-request cases, which is most of what ends up here. If GRIP later
# wants clinical-content redaction for secretaries specifically (e.g. a
# CLINICAL_QUESTION/SAFETY_RISK case's message bodies), that's a follow-up,
# not something this endpoint does today.


def _case_summary(conversation: Conversation) -> CaseSummaryOut:
    contact = conversation.contact
    return CaseSummaryOut(
        id=str(conversation.id),
        contact_id=str(contact.id),
        contact_name=contact.name or contact.whatsapp_profile_name,
        contact_phone=contact.phone,
        status=conversation.status.value,
        handoff_reason=conversation.handoff_reason,
        handoff_priority=conversation.handoff_priority,
        handoff_triggered_at=conversation.handoff_triggered_at.isoformat() if conversation.handoff_triggered_at else None,
        claimed_at=conversation.claimed_at.isoformat() if conversation.claimed_at else None,
        assigned_to=conversation.assigned_to,
        last_message_at=conversation.last_message_at.isoformat() if conversation.last_message_at else None,
    )


@router.get("/cases", response_model=list[CaseSummaryOut])
def list_cases(
    all: bool = False,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
) -> list[CaseSummaryOut]:
    """all=false (default): only conversations that were escalated to a person.
    all=true: every conversation with every patient (read-only audit view),
    most recent activity first."""
    query = db.query(Conversation).filter(Conversation.tenant_id == staff.tenant_id)
    if all:
        query = query.order_by(Conversation.last_message_at.desc().nullslast())
    else:
        query = query.filter(Conversation.handoff_reason.isnot(None)).order_by(
            Conversation.handoff_triggered_at.desc().nullslast()
        )
    return [_case_summary(c) for c in query.limit(300).all()]


@router.get("/cases/{conversation_id}", response_model=CaseDetailOut)
def get_case(conversation_id: str, staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)) -> CaseDetailOut:
    conversation = _get_case_or_404(db, staff, conversation_id)
    summary = _case_summary(conversation)
    return CaseDetailOut(
        **summary.model_dump(),
        messages=[
            CaseMessageOut(
                id=str(m.id),
                direction=m.direction.value,
                sender=m.sender.value,
                body=m.body,
                intent=m.intent,
                created_at=m.created_at.isoformat(),
            )
            for m in conversation.messages
        ],
        can_reply_freely=agent_reply.window_open(conversation),
        payment_appointment_id=_payment_appointment_id(db, conversation),
    )


def _payment_appointment_id(db: Session, conversation: Conversation) -> str | None:
    """The appointment a payment decision is about: one whose payment the
    patient reported, else the contact's next upcoming live appointment."""
    reported = (
        db.query(Appointment)
        .join(Payment, Payment.appointment_id == Appointment.id)
        .filter(Appointment.contact_id == conversation.contact_id, Payment.status == "REPORTED")
        .order_by(Appointment.scheduled_at.asc())
        .first()
    )
    if reported is not None:
        return str(reported.id)
    upcoming = (
        db.query(Appointment)
        .filter(
            Appointment.contact_id == conversation.contact_id,
            Appointment.status.in_(agenda.OCCUPYING),
            Appointment.scheduled_at >= datetime.now(timezone.utc) - timedelta(days=1),
        )
        .order_by(Appointment.scheduled_at.asc())
        .first()
    )
    return str(upcoming.id) if upcoming else None


def _case_action(db: Session, staff: Staff, conversation_id: str, fn) -> CaseDetailOut:
    """Runs one agent action on a case under a row lock, then returns the
    refreshed case. Domain errors come back as 400/409/502 with a Spanish message."""
    conversation = _get_case_or_404(db, staff, conversation_id)
    db.query(Conversation).filter(Conversation.id == conversation.id).with_for_update().one()
    try:
        fn(conversation)
        db.commit()
    except agent_reply.OutsideWindowError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except agent_reply.DeliveryFailedError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc))
    except (agent_reply.AgentReplyError, InvalidTransitionError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    return get_case(conversation_id, staff, db)


@router.post("/cases/{conversation_id}/claim", response_model=CaseDetailOut)
def claim_case(conversation_id: str, staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)) -> CaseDetailOut:
    return _case_action(db, staff, conversation_id, lambda c: agent_reply.claim(c, staff.display_name))


@router.post("/cases/{conversation_id}/reply", response_model=CaseDetailOut)
def reply_to_case(
    conversation_id: str,
    payload: CaseReplyRequest,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
) -> CaseDetailOut:
    return _case_action(db, staff, conversation_id, lambda c: agent_reply.send_reply(db, c, staff.display_name, payload.text))


@router.post("/cases/{conversation_id}/resolve", response_model=CaseDetailOut)
def resolve_case(
    conversation_id: str,
    payload: CaseResolveRequest,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
) -> CaseDetailOut:
    if payload.action == "return_to_bot":
        return _case_action(db, staff, conversation_id, lambda c: agent_reply.return_to_bot(c, staff.display_name))
    if payload.action == "close":
        return _case_action(db, staff, conversation_id, agent_reply.close)
    raise HTTPException(status_code=400, detail="Acción inválida.")