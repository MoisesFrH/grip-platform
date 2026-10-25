"""Shared dependencies for the CRM routers (login session, tenant and
patient/appointment lookups scoped to the staff member's own tenant)."""

import uuid
from datetime import datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.appointment import Appointment
from app.models.contact import Contact
from app.models.staff import Staff, StaffSession
from app.models.tenant import Tenant
from app.services import auth


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