"""
CRM routes for the agenda (week/day calendar), therapists, payments, the
monthly payment report (+CSV) and the price list. Mounted by crm.py under
/crm. Staff-authenticated, scoped to the staff member's tenant.

Access today: any logged-in staff member. When roles come back, the report /
price-list routes are the ones to restrict to administration — see
`require_report_access` below, the single place to change.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.routes.crm_deps import _get_appointment_or_404, _get_tenant, get_current_staff
from app.core.db import get_db
from app.models.appointment import Appointment
from app.models.billing import Payment, PriceItem
from app.models.staff import Staff
from app.services import agenda, payments

router = APIRouter()


def require_report_access(staff: Staff = Depends(get_current_staff)) -> Staff:
    """Reports and the price list. Open to every logged-in user for the demo;
    switch to an administration-only check when that role exists."""
    return staff


# --- agenda -------------------------------------------------------------------


class AgendaItem(BaseModel):
    id: str
    contact_id: str
    patient_name: str
    phone: str
    therapist_name: str
    start: str
    end: str
    duration_minutes: int
    status: str
    payment_status: str | None


class AgendaOut(BaseModel):
    start_date: str
    days: int
    therapists: list[str]
    hours: dict[str, list[int] | None]  # weekday (Mon=0) -> [open, close] or null
    appointments: list[AgendaItem]


@router.get("/agenda", response_model=AgendaOut)
def get_agenda(
    start: str = Query(..., description="First local day, YYYY-MM-DD"),
    days: int = Query(7, ge=1, le=7),
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
) -> AgendaOut:
    try:
        first = date.fromisoformat(start)
    except ValueError:
        raise HTTPException(status_code=400, detail="Fecha inválida (AAAA-MM-DD).")
    tenant = _get_tenant(db, staff)
    range_start = agenda.local_day_start_utc(first)
    range_end = agenda.local_day_start_utc(first + timedelta(days=days))

    rows = (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant.id,
            Appointment.scheduled_at >= range_start,
            Appointment.scheduled_at < range_end,
        )
        .order_by(Appointment.scheduled_at)
        .all()
    )
    pay_status = {
        p.appointment_id: p.status
        for p in db.query(Payment).filter(Payment.appointment_id.in_([a.id for a in rows] or [uuid.UUID(int=0)]))
    }
    items = [_agenda_item(a, pay_status.get(a.id)) for a in rows]
    hours = {str(k): (list(v) if v else None) for k, v in agenda.hours_map(tenant).items()}
    return AgendaOut(
        start_date=first.isoformat(),
        days=days,
        therapists=agenda.therapist_names(db, tenant, extra=[a.therapist_name for a in rows]),
        hours=hours,
        appointments=items,
    )


def _agenda_item(a: Appointment, pay_status: str | None) -> AgendaItem:
    minutes = a.duration_minutes or agenda.APPOINTMENT_MINUTES
    c = a.contact
    return AgendaItem(
        id=str(a.id),
        contact_id=str(a.contact_id),
        patient_name=c.name or c.whatsapp_profile_name or c.phone,
        phone=c.phone,
        therapist_name=a.therapist_name,
        start=a.scheduled_at.isoformat(),
        end=(a.scheduled_at + timedelta(minutes=minutes)).isoformat(),
        duration_minutes=minutes,
        status=a.status.value,
        payment_status=pay_status,
    )


@router.get("/appointments/{appointment_id}", response_model=AgendaItem)
def get_appointment(appointment_id: str, staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)) -> AgendaItem:
    appointment = _get_appointment_or_404(db, staff, appointment_id)
    payment = payments.get_payment(db, appointment)
    return _agenda_item(appointment, payment.status if payment else None)


@router.get("/therapists", response_model=list[str])
def list_therapists(staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)) -> list[str]:
    return agenda.therapist_names(db, _get_tenant(db, staff))


# --- payments ---------------------------------------------------------------------


class PaymentOut(BaseModel):
    appointment_id: str
    exists: bool
    amount: float | None = None
    method: str = "TRANSFER"
    status: str = "PENDING"
    reference: str | None = None
    note: str | None = None
    reported_at: str | None = None
    verified_by: str | None = None
    verified_at: str | None = None


class PaymentIn(BaseModel):
    amount: float | str | None = None
    method: str = "TRANSFER"
    status: str = "PENDING"
    reference: str | None = None
    note: str | None = None
    notify_patient: bool = False


class PaymentSaveOut(PaymentOut):
    patient_notified: bool = False


def _payment_out(appointment_id, payment: Payment | None) -> PaymentOut:
    if payment is None:
        return PaymentOut(appointment_id=str(appointment_id), exists=False)
    return PaymentOut(
        appointment_id=str(appointment_id),
        exists=True,
        amount=float(payment.amount) if payment.amount is not None else None,
        method=payment.method,
        status=payment.status,
        reference=payment.reference,
        note=payment.note,
        reported_at=payment.reported_at.isoformat() if payment.reported_at else None,
        verified_by=payment.verified_by,
        verified_at=payment.verified_at.isoformat() if payment.verified_at else None,
    )


@router.get("/appointments/{appointment_id}/payment", response_model=PaymentOut)
def get_appointment_payment(
    appointment_id: str, staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)
) -> PaymentOut:
    appointment = _get_appointment_or_404(db, staff, appointment_id)
    return _payment_out(appointment.id, payments.get_payment(db, appointment))


@router.put("/appointments/{appointment_id}/payment", response_model=PaymentSaveOut)
def save_appointment_payment(
    appointment_id: str,
    payload: PaymentIn,
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
) -> PaymentSaveOut:
    appointment = _get_appointment_or_404(db, staff, appointment_id)
    tenant = _get_tenant(db, staff)
    try:
        payment = payments.save_payment(
            db, tenant, appointment,
            amount=payload.amount, method=payload.method, status=payload.status,
            reference=payload.reference, note=payload.note, actor=staff.display_name,
        )
    except payments.PaymentError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    notified = False
    if payload.notify_patient and payment.status == "VERIFIED":
        notified = payments.notify_verified(db, tenant, appointment.contact, appointment)
    db.commit()
    out = _payment_out(appointment.id, payment)
    return PaymentSaveOut(**out.model_dump(), patient_notified=notified)


class PaymentRowOut(BaseModel):
    appointment_id: str
    scheduled_at: str
    patient: str
    therapist_name: str
    amount: float | None
    method: str
    status: str
    reference: str | None
    verified_by: str | None


class BucketOut(BaseModel):
    count: int
    amount: float


class ReportOut(BaseModel):
    month: str
    rows: list[PaymentRowOut]
    by_status: dict[str, BucketOut]
    verified_by_method: dict[str, BucketOut]
    verified_by_therapist: dict[str, BucketOut]
    without_amount: int


def _bucket_out(d: dict) -> dict[str, BucketOut]:
    return {k: BucketOut(count=v["count"], amount=float(v["amount"])) for k, v in d.items()}


def _current_month() -> str:
    return agenda.to_local(datetime.now(timezone.utc)).strftime("%Y-%m")


def _rows_or_400(db, tenant, month, status, therapist):
    try:
        return payments.list_rows(db, tenant, month, status=status or None, therapist=therapist or None)
    except payments.PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/payments", response_model=ReportOut)
def payments_report(
    month: str | None = None,
    status: str | None = None,
    therapist: str | None = None,
    staff: Staff = Depends(require_report_access),
    db: Session = Depends(get_db),
) -> ReportOut:
    """Rows + totals for a month (default: current). Totals always cover the
    whole month, ignoring the status/therapist filters that narrow `rows`."""
    tenant = _get_tenant(db, staff)
    month = month or _current_month()
    all_rows = _rows_or_400(db, tenant, month, None, therapist)
    shown = [r for r in all_rows if not status or r["payment"].status == status]
    report = payments.build_report(all_rows)
    return ReportOut(
        month=month,
        rows=[
            PaymentRowOut(
                appointment_id=str(r["appointment"].id),
                scheduled_at=r["appointment"].scheduled_at.isoformat(),
                patient=r["patient"],
                therapist_name=r["appointment"].therapist_name,
                amount=float(r["payment"].amount) if r["payment"].amount is not None else None,
                method=r["payment"].method,
                status=r["payment"].status,
                reference=r["payment"].reference,
                verified_by=r["payment"].verified_by,
            )
            for r in shown
        ],
        by_status=_bucket_out(report["by_status"]),
        verified_by_method=_bucket_out(report["verified_by_method"]),
        verified_by_therapist=_bucket_out(report["verified_by_therapist"]),
        without_amount=report["without_amount"],
    )


@router.get("/payments/export.csv")
def payments_csv(
    month: str | None = None,
    staff: Staff = Depends(require_report_access),
    db: Session = Depends(get_db),
) -> Response:
    tenant = _get_tenant(db, staff)
    month = month or _current_month()
    body = payments.to_csv(_rows_or_400(db, tenant, month, None, None))
    return Response(
        content="﻿" + body,  # BOM so Excel opens accents correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="pagos-{month}.csv"'},
    )


# --- price list ----------------------------------------------------------------------


class PriceItemOut(BaseModel):
    id: str
    name: str
    amount: float | None
    is_active: bool


class PriceItemIn(BaseModel):
    name: str | None = None
    amount: float | str | None = None
    is_active: bool | None = None


def _price_out(p: PriceItem) -> PriceItemOut:
    return PriceItemOut(id=str(p.id), name=p.name, amount=float(p.amount) if p.amount is not None else None, is_active=p.is_active)


@router.get("/price-items", response_model=list[PriceItemOut])
def list_price_items(staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)) -> list[PriceItemOut]:
    """Readable by everyone (it pre-fills the payment form); only the PUT/POST
    below are meant for administration."""
    tenant = _get_tenant(db, staff)
    payments.ensure_price_items(db, tenant)
    db.commit()
    rows = db.query(PriceItem).filter(PriceItem.tenant_id == tenant.id).order_by(PriceItem.name).all()
    return [_price_out(p) for p in rows]


@router.post("/price-items", response_model=PriceItemOut)
def create_price_item(payload: PriceItemIn, staff: Staff = Depends(require_report_access), db: Session = Depends(get_db)) -> PriceItemOut:
    tenant = _get_tenant(db, staff)
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Escribe el nombre del servicio.")
    if db.query(PriceItem).filter(PriceItem.tenant_id == tenant.id, PriceItem.name == name).first():
        raise HTTPException(status_code=400, detail="Ya existe un servicio con ese nombre.")
    try:
        item = PriceItem(tenant_id=tenant.id, name=name, amount=payments.parse_amount(payload.amount))
    except payments.PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.add(item)
    db.commit()
    return _price_out(item)


@router.put("/price-items/{item_id}", response_model=PriceItemOut)
def update_price_item(
    item_id: str, payload: PriceItemIn, staff: Staff = Depends(require_report_access), db: Session = Depends(get_db)
) -> PriceItemOut:
    tenant = _get_tenant(db, staff)
    try:
        item_uuid = uuid.UUID(item_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Servicio no encontrado.")
    item = db.query(PriceItem).filter(PriceItem.id == item_uuid, PriceItem.tenant_id == tenant.id).one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Servicio no encontrado.")
    try:
        if "amount" in payload.model_fields_set:
            item.amount = payments.parse_amount(payload.amount)
    except payments.PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if payload.name is not None and payload.name.strip():
        item.name = payload.name.strip()
    if payload.is_active is not None:
        item.is_active = payload.is_active
    db.commit()
    return _price_out(item)