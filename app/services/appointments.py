"""
Appointment scheduling data + the day-before reminder flow.

Two separate concerns live here, deliberately kept in one small module
because they share the same table:

1. Plain CRUD-ish helpers around Appointment (create / list / mark status).
2. The reminder pipeline: who needs a reminder today, and — critically —
   detecting when a patient's reply to a reminder is a cancel/reschedule
   request, which must ALWAYS reach a human (never something the bot
   decides on its own). That detection is a deterministic keyword check
   run *before* Gemini intent classification even starts, so it cannot be
   argued away by a persuasive message or a Gemini misclassification —
   same fail-closed philosophy as app.services.safety.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.appointment import Appointment, AppointmentStatus, ReminderResponse
from app.models.contact import Contact
from app.models.message import MessageSender
from app.models.tenant import Tenant
from app.services import crm
from app.services.messaging import send_whatsapp_message

# Deliberately broad and Dominican-Spanish-flavored; a false positive here
# just means an ordinary reply gets a human's eyes on it too, which is the
# safe direction to err in. A false negative — a real cancellation the bot
# tries to handle itself — is the outcome this whole module exists to
# prevent, so the list stays generous rather than "precise."
_CANCEL_OR_RESCHEDULE_KEYWORDS = [
    "cancelar",
    "cancela",
    "cancelo",
    "cancelacion",
    "cancelación",
    "no puedo ir",
    "no podre",
    "no podré",
    "no voy a poder",
    "no voy a asistir",
    "no voy a ir",
    "reprogramar",
    "reprograma",
    "re-agendar",
    "reagendar",
    "reagenda",
    "cambiar la cita",
    "cambiar mi cita",
    "cambiar de fecha",
    "cambiar de hora",
    "mover la cita",
    "mover mi cita",
    "moverla",
    "aplazar",
    "posponer",
    "pospon",
    "otro dia",
    "otro día",
    "otra fecha",
    "otra hora",
]

_CONFIRM_KEYWORDS = [
    "si",
    "sí",
    "confirmo",
    "confirmado",
    "confirmada",
    "ok",
    "okay",
    "vale",
    "ahi estare",
    "ahí estaré",
    "alli estare",
    "allí estaré",
    "asistire",
    "asistiré",
    "claro",
    "perfecto",
]

REMINDER_WINDOW_HOURS = 48  # how long after sending a reminder we still treat a reply as "about the reminder"

APPOINTMENT_REMINDER_TEMPLATE = (
    "Hola{name_suffix}, te recordamos tu cita mañana {when} con {therapist} en el GRIP. "
    "Si necesitas cancelar o cambiar la cita, respóndenos aquí y te ayuda alguien de nuestro equipo. "
    "Si todo está bien, solo responde \"Sí\" para confirmar."
)

RESCHEDULE_HANDOFF_MESSAGE = (
    "Entiendo, gracias por avisar. Voy a conectarte con nuestro equipo para que te ayuden "
    "a cancelar o mover tu cita. En un momento te responden por aquí mismo."
)

APPOINTMENT_CONFIRMED_REPLY = "¡Perfecto, gracias por confirmar! Te esperamos."


def _normalize(text: str) -> str:
    return " " + text.strip().lower() + " "


def create_appointment(
    db: Session,
    tenant: Tenant,
    contact: Contact,
    *,
    therapist_name: str,
    scheduled_at: datetime,
) -> Appointment:
    appointment = Appointment(
        tenant_id=tenant.id,
        contact_id=contact.id,
        therapist_name=therapist_name,
        scheduled_at=scheduled_at,
        status=AppointmentStatus.SCHEDULED,
    )
    db.add(appointment)
    db.flush()
    return appointment


def appointments_needing_reminder(db: Session, tenant: Tenant, *, reference: datetime | None = None) -> list[Appointment]:
    """Appointments scheduled for "tomorrow" (relative to `reference`, UTC)
    that haven't had a reminder sent yet and are still on the books
    (SCHEDULED or CONFIRMED — not already cancelled). Meant to be called
    once a day by scripts/send_appointment_reminders.py."""
    reference = reference or datetime.now(timezone.utc)
    window_start = reference + timedelta(hours=24)
    window_end = reference + timedelta(hours=48)

    return (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant.id,
            Appointment.scheduled_at >= window_start,
            Appointment.scheduled_at < window_end,
            Appointment.status.in_([AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED]),
            Appointment.reminder_sent_at.is_(None),
        )
        .all()
    )


def mark_reminder_sent(appointment: Appointment, *, at: datetime | None = None) -> None:
    appointment.reminder_sent_at = at or datetime.now(timezone.utc)


def find_pending_reminder_for_contact(db: Session, tenant: Tenant, contact: Contact) -> Appointment | None:
    """The most recent appointment for this contact whose reminder was
    sent recently and hasn't gotten a reply yet — i.e. "is this inbound
    message plausibly a reply to a reminder we just sent?" Used by the
    orchestrator to decide whether to run the cancel/reschedule keyword
    check at all."""
    since = datetime.now(timezone.utc) - timedelta(hours=REMINDER_WINDOW_HOURS)
    return (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant.id,
            Appointment.contact_id == contact.id,
            Appointment.reminder_sent_at.isnot(None),
            Appointment.reminder_sent_at >= since,
            Appointment.reminder_response.is_(None),
            Appointment.status.in_([AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED]),
        )
        .order_by(Appointment.scheduled_at.asc())
        .first()
    )


def classify_reminder_reply(body: str) -> ReminderResponse | None:
    """Deterministic keyword check — no Gemini, no ambiguity. Cancel/
    reschedule signals are checked first, since "sí, pero tengo que
    cambiar la hora" should route to a human, not get auto-confirmed."""
    normalized = _normalize(body)
    if any(keyword in normalized for keyword in _CANCEL_OR_RESCHEDULE_KEYWORDS):
        return ReminderResponse.CANCEL_OR_RESCHEDULE
    if any(f" {keyword} " in normalized or normalized.strip() == keyword for keyword in _CONFIRM_KEYWORDS):
        return ReminderResponse.CONFIRMED
    return None


def apply_reminder_reply(appointment: Appointment, response: ReminderResponse) -> None:
    appointment.reminder_response = response
    if response == ReminderResponse.CONFIRMED:
        appointment.status = AppointmentStatus.CONFIRMED
    elif response == ReminderResponse.CANCEL_OR_RESCHEDULE:
        appointment.status = AppointmentStatus.RESCHEDULE_REQUESTED


# Appointment times are shown to the patient in the clinic's own local
# time, not whatever timezone the staff member happened to be in when they
# scheduled it (GRIP's staff can be anywhere — Dome herself is in the UK).
# scheduled_at is stored timezone-aware (in UTC) in the database, so this
# conversion is what actually fixes "the reminder said 1pm but the cita is
# at 2pm": without it, _format_when was printing the raw UTC clock time.
# Santo Domingo has no daylight saving time, so this offset never changes.
CLINIC_TIMEZONE = ZoneInfo("America/Santo_Domingo")


def _format_when(scheduled_at: datetime) -> str:
    # Spanish, no year (it's always "tomorrow" in the real reminder job):
    # "a las 3:00 p. m."
    local_time = scheduled_at.astimezone(CLINIC_TIMEZONE)
    return local_time.strftime("a las %I:%M %p").lower().replace("am", "a. m.").replace("pm", "p. m.")


def build_reminder_message(appointment: Appointment, contact: Contact) -> str:
    name_suffix = f" {contact.name}" if contact.name else ""
    return APPOINTMENT_REMINDER_TEMPLATE.format(
        name_suffix=name_suffix,
        when=_format_when(appointment.scheduled_at),
        therapist=appointment.therapist_name,
    )


def send_reminder(db: Session, tenant: Tenant, appointment: Appointment, contact: Contact) -> str:
    """The one place that actually sends a reminder: builds the message,
    records it as an outbound SYSTEM message (so it shows up in the
    conversation transcript like any other message), marks the
    appointment's reminder as sent, and calls the messaging stub. Shared
    by scripts/send_appointment_reminders.py (the real daily job) and the
    simulator's "probar recordatorio" button (app.api.routes.simulator) —
    same code path either way, so testing it in the simulator actually
    exercises the real logic, not a parallel copy of it."""
    body = build_reminder_message(appointment, contact)
    conversation = crm.get_or_create_open_conversation(db, tenant, contact)
    crm.record_outbound_message(db, conversation, body, sender=MessageSender.SYSTEM)
    mark_reminder_sent(appointment)
    send_whatsapp_message(contact.phone, body)
    return body