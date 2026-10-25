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

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.appointment import Appointment, AppointmentStatus, ReminderResponse
from app.models.contact import Contact
from app.models.message import MessageSender
from app.models.tenant import Tenant
from app.services import crm, meta_whatsapp, reminder_summary
from app.services.messaging import send_whatsapp_message

logger = logging.getLogger(__name__)

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

# Automatic reminders go out when the appointment is this far away.
REMINDER_KINDS = ("48h", "24h")
MIN_LEAD_HOURS = 2  # never send an automatic reminder for an appointment starting in under 2h

# FICTITIOUS placeholder bank details. Replace with the clinic's real data in
# Tenant.config["payment"] (same keys) before going live. The "fictitious"
# flag makes the placeholder obvious to staff and in logs.
DEFAULT_PAYMENT = {
    "bank_name": "Banco Ficticio de Prueba",
    "account_holder": "ND Neurodiversa (CUENTA DE PRUEBA)",
    "account_type": "Cuenta corriente",
    "account_number": "000-000000-0",
    "fictitious": True,
}

_PAY_KEYWORDS = ["pagar", "pago", "transferir", "transferencia", "cuenta", "banco"]
_PAID_KEYWORDS = ["ya pague", "ya pagué", "ya transferi", "ya transferí", "pague", "pagué", "transferi", "transferí", "comprobante"]

APPOINTMENT_REMINDER_TEMPLATE = (
    "Hola{name_suffix}, te recordamos tu cita {when} con {therapist} en ND Neurodiversa.\n\n"
    "En esta sesión vamos a trabajar en {summary}.\n\n"
    "¿Deseas adelantar el pago de la sesión por transferencia? Responde \"PAGAR\" y te enviamos los datos de la cuenta.\n\n"
    "Si todo está bien, responde \"Sí\" para confirmar. Si necesitas cancelar o cambiar la cita, "
    "respóndenos aquí y te ayuda alguien de nuestro equipo."
)

# Body to register in Meta (WhatsApp Manager > Message templates), category
# UTILITY, language Spanish (es). Parameters, in order:
# {{1}} nombre, {{2}} cuándo, {{3}} terapeuta, {{4}} qué se trabajará.
META_TEMPLATE_BODY = (
    "Hola {{1}}, te recordamos tu cita {{2}} con {{3}} en ND Neurodiversa. "
    "En esta sesión vamos a trabajar en {{4}}. "
    "¿Deseas adelantar el pago de la sesión por transferencia? Responde PAGAR y te enviamos los datos de la cuenta. "
    "Si todo está bien, responde Sí para confirmar. Si necesitas cancelar o cambiar la cita, respóndenos aquí."
)

RESCHEDULE_HANDOFF_MESSAGE = (
    "Entiendo, gracias por avisar. Voy a conectarte con nuestro equipo para que te ayuden "
    "a cancelar o mover tu cita. En un momento te responden por aquí mismo."
)

APPOINTMENT_CONFIRMED_REPLY = "¡Perfecto, gracias por confirmar! Te esperamos."

PAYMENT_RECEIVED_HANDOFF_MESSAGE = (
    "¡Gracias! Nuestro equipo va a verificar tu transferencia y te confirma por aquí mismo."
)


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


def reminders_due(db: Session, tenant: Tenant, *, reference: datetime | None = None) -> list[tuple[Appointment, str]]:
    """(appointment, kind) pairs that should get an automatic reminder now.
    Meant to run hourly (scripts/send_appointment_reminders.py).

    * "48h": starts in more than 24h and at most 48h, 48h reminder not sent yet
      (and the 24h one not sent either — never send them out of order).
    * "24h": starts within the next 24h (but not in under MIN_LEAD_HOURS),
      24h reminder not sent yet.
    Only SCHEDULED / CONFIRMED appointments; cancelled ones are skipped."""
    reference = reference or datetime.now(timezone.utc)
    live = [AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED]
    base = db.query(Appointment).filter(Appointment.tenant_id == tenant.id, Appointment.status.in_(live))

    due: list[tuple[Appointment, str]] = []
    for appt in base.filter(
        Appointment.scheduled_at > reference + timedelta(hours=24),
        Appointment.scheduled_at <= reference + timedelta(hours=48),
        Appointment.reminder_48h_sent_at.is_(None),
        Appointment.reminder_24h_sent_at.is_(None),
    ).all():
        due.append((appt, "48h"))
    for appt in base.filter(
        Appointment.scheduled_at > reference + timedelta(hours=MIN_LEAD_HOURS),
        Appointment.scheduled_at <= reference + timedelta(hours=24),
        Appointment.reminder_24h_sent_at.is_(None),
    ).all():
        due.append((appt, "24h"))
    return due


def next_appointment_for_contact(db: Session, tenant: Tenant, contact: Contact) -> Appointment | None:
    """The contact's soonest upcoming appointment that is still on the books."""
    return (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant.id,
            Appointment.contact_id == contact.id,
            Appointment.status.in_([AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED]),
            Appointment.scheduled_at >= datetime.now(timezone.utc),
        )
        .order_by(Appointment.scheduled_at.asc())
        .first()
    )


def describe_appointment(appointment: Appointment) -> str:
    """"mañana a las 3:00 p. m. con Esther Jimenez" (clinic time)."""
    return f"{_format_when(appointment.scheduled_at)} con {appointment.therapist_name}"


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


def wants_to_pay(body: str) -> bool:
    """The patient answered the reminder's payment question ("PAGAR")."""
    normalized = _normalize(body)
    if any(k in normalized for k in _CANCEL_OR_RESCHEDULE_KEYWORDS):
        return False
    if any(k in normalized for k in _PAID_KEYWORDS):
        return False
    return any(f" {k}" in normalized for k in _PAY_KEYWORDS)


def says_already_paid(body: str) -> bool:
    return any(k in _normalize(body) for k in _PAID_KEYWORDS)


def payment_details(tenant: Tenant) -> dict:
    return {**DEFAULT_PAYMENT, **((tenant.config or {}).get("payment") or {})}


def payment_details_message(tenant: Tenant) -> str:
    p = payment_details(tenant)
    note = "\n(Datos de prueba, no realices transferencias reales.)" if p.get("fictitious") else ""
    return (
        "¡Con gusto! Estos son los datos para la transferencia:\n\n"
        f"Banco: {p['bank_name']}\n"
        f"Titular: {p['account_holder']}\n"
        f"Tipo de cuenta: {p['account_type']}\n"
        f"Número de cuenta: {p['account_number']}\n"
        f"{note}\n"
        "Cuando hayas transferido, escríbenos \"ya pagué\" y nuestro equipo lo verifica."
    ).replace("\n\n\n", "\n\n")


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


_WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
           "septiembre", "octubre", "noviembre", "diciembre"]


def _format_time(local_time: datetime) -> str:
    return local_time.strftime("%I:%M %p").lstrip("0").lower().replace("am", "a. m.").replace("pm", "p. m.")


def _format_when(scheduled_at: datetime, *, now: datetime | None = None) -> str:
    """"mañana a las 3:00 p. m." or "el viernes 24 de octubre a las 3:00 p. m."
    (always in the clinic's local time)."""
    local = scheduled_at.astimezone(CLINIC_TIMEZONE)
    today = (now or datetime.now(timezone.utc)).astimezone(CLINIC_TIMEZONE).date()
    days = (local.date() - today).days
    time_part = f"a las {_format_time(local)}"
    if days == 0:
        return f"hoy {time_part}"
    if days == 1:
        return f"mañana {time_part}"
    return f"el {_WEEKDAYS[local.weekday()]} {local.day} de {_MONTHS[local.month - 1]} {time_part}"


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class ReminderContent:
    name: str | None
    when: str
    therapist: str
    summary: str

    def text(self) -> str:
        return APPOINTMENT_REMINDER_TEMPLATE.format(
            name_suffix=f" {self.name}" if self.name else "",
            when=self.when,
            therapist=self.therapist,
            summary=self.summary,
        )

    def template_params(self) -> list[str]:
        return [_one_line(p) for p in (self.name or "paciente", self.when, self.therapist, self.summary)]


def build_reminder_content(appointment: Appointment, contact: Contact, *, summary: str | None = None) -> ReminderContent:
    return ReminderContent(
        name=contact.name,
        when=_format_when(appointment.scheduled_at),
        therapist=appointment.therapist_name,
        summary=summary or reminder_summary.GENERIC_SUMMARY,
    )


def build_reminder_message(appointment: Appointment, contact: Contact, *, summary: str | None = None) -> str:
    return build_reminder_content(appointment, contact, summary=summary).text()


class ReminderDeliveryError(Exception):
    """The reminder could not be delivered; nothing was recorded or marked."""


def _deliver(contact: Contact, content: ReminderContent) -> bool:
    """Real WhatsApp (Meta) when configured, otherwise the logging stub.
    Outside the 24h window Meta only accepts an approved template, so when
    META_REMINDER_TEMPLATE is set that is what is sent."""
    settings = get_settings()
    if settings.meta_access_token and settings.meta_phone_number_id:
        if settings.meta_reminder_template:
            return meta_whatsapp.send_template(
                settings.meta_access_token, settings.meta_phone_number_id, contact.phone,
                settings.meta_reminder_template, settings.meta_template_language, content.template_params(),
            )
        return meta_whatsapp.send_text(
            settings.meta_access_token, settings.meta_phone_number_id, contact.phone, content.text()
        )
    send_whatsapp_message(contact.phone, content.text())
    return True


def send_reminder(
    db: Session,
    tenant: Tenant,
    appointment: Appointment,
    contact: Contact,
    *,
    kind: str = "manual",
    summary: str | None = None,
    use_notes_summary: bool = False,
) -> str:
    """The one place that actually sends a reminder (cron job, CRM button and
    the simulator all come through here). Delivers first; only if delivery
    works does it record the SYSTEM message and mark the reminder as sent, so
    a failed send is retried by the next cron run instead of being lost.

    kind: "48h" / "24h" (automatic, marks that flag) or "manual" (CRM button,
    marks nothing but reminder_sent_at, so it never blocks the automatic ones).
    use_notes_summary: build the one-sentence summary from the CRM notes."""
    if summary is None and use_notes_summary:
        summary = reminder_summary.summary_for_contact(db, tenant, contact)
    content = build_reminder_content(appointment, contact, summary=summary)
    body = content.text()

    if not _deliver(contact, content):
        raise ReminderDeliveryError(f"No se pudo enviar el recordatorio a {contact.phone}")

    conversation = crm.get_or_create_open_conversation(db, tenant, contact)
    crm.record_outbound_message(db, conversation, body, sender=MessageSender.SYSTEM)
    now = datetime.now(timezone.utc)
    mark_reminder_sent(appointment, at=now)
    if kind == "48h":
        appointment.reminder_48h_sent_at = now
    elif kind == "24h":
        appointment.reminder_24h_sent_at = now
    # A new reminder opens a new question: forget an earlier "Sí" so the
    # reply guard watches the next answer (a later cancel must still reach a human).
    if appointment.reminder_response == ReminderResponse.CONFIRMED:
        appointment.reminder_response = None
    return body