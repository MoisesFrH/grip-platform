"""
Payments per appointment, the price list and the monthly report.

One Payment row per appointment. PENDING -> REPORTED (the patient wrote "ya
pagué", set automatically by the orchestrator) -> VERIFIED / REJECTED (staff
checked the bank). Money amounts are Decimal in the database.
"""

import csv
import io
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models.appointment import Appointment
from app.models.billing import PAYMENT_METHODS, PAYMENT_STATUSES, Payment, PriceItem
from app.models.contact import Contact
from app.models.message import Message, MessageDirection, MessageSender
from app.models.conversation import Conversation
from app.models.tenant import Tenant
from app.services import agenda, crm

METHOD_LABELS = {
    "TRANSFER": "Transferencia", "CASH": "Efectivo", "CARD": "Tarjeta",
    "SENASA": "SeNaSa", "PATRONATO": "Patronato", "OTHER": "Otro",
}
STATUS_LABELS = {"PENDING": "Pendiente", "REPORTED": "Informado", "VERIFIED": "Verificado", "REJECTED": "Rechazado"}


class PaymentError(Exception):
    """Message is safe to show to staff (Spanish)."""


def parse_amount(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise PaymentError("El monto no es válido.")
    if amount < 0:
        raise PaymentError("El monto no puede ser negativo.")
    return amount


def get_payment(db: Session, appointment: Appointment) -> Payment | None:
    return db.query(Payment).filter(Payment.appointment_id == appointment.id).one_or_none()


def save_payment(
    db: Session,
    tenant: Tenant,
    appointment: Appointment,
    *,
    amount,
    method: str,
    status: str,
    reference: str | None,
    note: str | None,
    actor: str,
) -> Payment:
    if method not in PAYMENT_METHODS:
        raise PaymentError("Forma de pago inválida.")
    if status not in PAYMENT_STATUSES:
        raise PaymentError("Estado de pago inválido.")
    payment = get_payment(db, appointment)
    if payment is None:
        payment = Payment(tenant_id=tenant.id, appointment_id=appointment.id)
        db.add(payment)
    previous_status = payment.status
    payment.amount = parse_amount(amount)
    payment.method = method
    payment.status = status
    payment.reference = (reference or "").strip() or None
    payment.note = (note or "").strip() or None
    now = datetime.now(timezone.utc)
    if status == "REPORTED" and previous_status != "REPORTED":
        payment.reported_at = now
    if status in ("VERIFIED", "REJECTED"):
        if previous_status != status or payment.verified_at is None:
            payment.verified_by = actor
            payment.verified_at = now
    else:
        payment.verified_by = None
        payment.verified_at = None
    db.flush()
    return payment


def mark_reported(db: Session, tenant: Tenant, appointment: Appointment) -> Payment:
    """The patient said they paid (bot reply guard). Never downgrades a
    payment that staff already verified."""
    payment = get_payment(db, appointment)
    if payment is None:
        payment = Payment(tenant_id=tenant.id, appointment_id=appointment.id, method="TRANSFER")
        db.add(payment)
    if payment.status in ("PENDING", "REJECTED") or payment.status is None:
        payment.status = "REPORTED"
        payment.reported_at = datetime.now(timezone.utc)
        payment.verified_by = None
        payment.verified_at = None
    db.flush()
    return payment


def notify_verified(db: Session, tenant: Tenant, contact: Contact, appointment: Appointment) -> bool:
    """Tells the patient their payment was verified — only possible inside
    WhatsApp's 24h window (free text). Returns whether it was sent."""
    from app.services import agent_reply  # local import: avoids a cycle at load time

    last_inbound = (
        db.query(Message.created_at)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(Conversation.contact_id == contact.id, Message.direction == MessageDirection.INBOUND)
        .order_by(Message.created_at.desc())
        .first()
    )
    if last_inbound is None or datetime.now(timezone.utc) - last_inbound[0] > timedelta(hours=agent_reply.WINDOW_HOURS):
        return False
    from app.services.appointments import _format_when

    name = f" {contact.name}" if contact.name else ""
    text = f"Hola{name}, confirmamos que recibimos tu pago de la cita {_format_when(appointment.scheduled_at)}. ¡Gracias!"
    if not agent_reply._deliver(contact, text):
        return False
    conversation = crm.get_or_create_open_conversation(db, tenant, contact)
    crm.record_outbound_message(db, conversation, text, sender=MessageSender.SYSTEM)
    return True


# --- price list ------------------------------------------------------------


def ensure_price_items(db: Session, tenant: Tenant) -> None:
    """Creates a blank (amount NULL) line per service in the tenant config the
    first time; afterwards the table is the source of truth."""
    existing = {p.name for p in db.query(PriceItem).filter(PriceItem.tenant_id == tenant.id)}
    for service in (tenant.config or {}).get("services", []) or []:
        name = service.get("name") if isinstance(service, dict) else None
        if name and name not in existing:
            db.add(PriceItem(tenant_id=tenant.id, name=name))
            existing.add(name)
    db.flush()


# --- listing & reports -------------------------------------------------------

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def month_bounds_utc(month: str) -> tuple[datetime, datetime]:
    match = _MONTH_RE.match(month or "")
    if not match:
        raise PaymentError("El mes debe tener el formato AAAA-MM.")
    year, mon = int(match.group(1)), int(match.group(2))
    first = date(year, mon, 1)
    nxt = date(year + (mon == 12), (mon % 12) + 1, 1)
    return agenda.local_day_start_utc(first), agenda.local_day_start_utc(nxt)


def list_rows(db: Session, tenant: Tenant, month: str, *, status: str | None = None, therapist: str | None = None) -> list[dict]:
    start, end = month_bounds_utc(month)
    query = (
        db.query(Payment, Appointment, Contact)
        .join(Appointment, Appointment.id == Payment.appointment_id)
        .join(Contact, Contact.id == Appointment.contact_id)
        .filter(Payment.tenant_id == tenant.id, Appointment.scheduled_at >= start, Appointment.scheduled_at < end)
        .order_by(Appointment.scheduled_at.desc())
    )
    if status:
        query = query.filter(Payment.status == status)
    if therapist:
        query = query.filter(Appointment.therapist_name == therapist)
    rows = []
    for payment, appointment, contact in query.all():
        rows.append({
            "payment": payment,
            "appointment": appointment,
            "patient": contact.name or contact.whatsapp_profile_name or contact.phone,
        })
    return rows


def build_report(rows: list[dict]) -> dict:
    """Totals for the month. REJECTED payments are listed but never counted
    as money. Amounts of NULL count as 0 and are reported as `without_amount`."""
    def bucket() -> dict:
        return {"count": 0, "amount": Decimal("0.00")}

    by_status = {s: bucket() for s in PAYMENT_STATUSES}
    by_method: dict[str, dict] = {}
    by_therapist: dict[str, dict] = {}
    without_amount = 0
    for row in rows:
        p: Payment = row["payment"]
        amount = p.amount or Decimal("0.00")
        by_status[p.status]["count"] += 1
        by_status[p.status]["amount"] += amount
        if p.amount is None:
            without_amount += 1
        if p.status == "VERIFIED":
            for key, store in ((p.method, by_method), (row["appointment"].therapist_name, by_therapist)):
                b = store.setdefault(key, bucket())
                b["count"] += 1
                b["amount"] += amount
    return {
        "by_status": by_status,
        "verified_by_method": by_method,
        "verified_by_therapist": by_therapist,
        "without_amount": without_amount,
    }


def to_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Fecha", "Hora", "Paciente", "Terapeuta", "Monto", "Forma de pago", "Estado", "Referencia", "Verificado por", "Verificado el"])
    for row in rows:
        p: Payment = row["payment"]
        a: Appointment = row["appointment"]
        local = agenda.to_local(a.scheduled_at)
        verified_at = agenda.to_local(p.verified_at).strftime("%Y-%m-%d %H:%M") if p.verified_at else ""
        writer.writerow([
            local.strftime("%Y-%m-%d"), local.strftime("%H:%M"), _csv_safe(row["patient"]), _csv_safe(a.therapist_name),
            f"{p.amount:.2f}" if p.amount is not None else "",
            METHOD_LABELS.get(p.method, p.method), STATUS_LABELS.get(p.status, p.status),
            _csv_safe(p.reference or ""), _csv_safe(p.verified_by or ""), verified_at,
        ])
    return buffer.getvalue()


def _csv_safe(value: str) -> str:
    """Spreadsheet formula injection guard: a cell starting with = + - @ would
    be executed by Excel, and patient names / references are user-typed."""
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value