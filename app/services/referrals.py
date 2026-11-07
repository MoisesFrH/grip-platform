"""
Referral tracking ("¿quién te refirió / cómo nos escuchaste?").

When the source is an existing patient, that patient earns a discount once the
REFERRED patient signs the consent (= really starts treatment). The discount
itself is not defined yet, so Administration records what was given as free
text when marking it applied.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.consent import ConsentRecord
from app.models.contact import Contact
from app.models.referral import Referral

PATIENT = "PATIENT"
SOURCES = {
    PATIENT: "Un paciente actual",
    "RELATIVE_FRIEND": "Familiar o amistad (no es paciente)",
    "SOCIAL": "Redes sociales",
    "SEARCH": "Google / búsqueda en internet",
    "SCHOOL_DOCTOR": "Escuela, pediatra u otro profesional",
    "OTHER": "Otro",
}

NONE = "NONE"
PENDING_CONSENT = "PENDING_CONSENT"
EARNED = "EARNED"
APPLIED = "APPLIED"
REWARD_LABELS = {
    NONE: "Sin descuento",
    PENDING_CONSENT: "Descuento pendiente de que el referido firme",
    EARNED: "Descuento ganado",
    APPLIED: "Descuento aplicado",
}


class ReferralError(ValueError):
    pass


def _consent_signed(db: Session, contact: Contact) -> bool:
    row = db.query(ConsentRecord).filter(ConsentRecord.contact_id == contact.id).one_or_none()
    return row is not None and row.signed_at is not None


def set_referral(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    referred: Contact,
    source: str,
    referrer: Contact | None,
    detail: str | None,
    staff_name: str | None,
) -> Referral:
    if source not in SOURCES:
        raise ReferralError("Fuente no válida.")
    if source == PATIENT:
        if referrer is None:
            raise ReferralError("Elige qué paciente lo refirió.")
        if referrer.id == referred.id:
            raise ReferralError("Un paciente no puede referirse a sí mismo.")
    else:
        referrer = None

    row = db.query(Referral).filter(Referral.referred_contact_id == referred.id).one_or_none()
    if row is not None and row.reward_status == APPLIED:
        raise ReferralError("El descuento de esta referencia ya fue aplicado; no se puede cambiar.")
    if row is None:
        row = Referral(tenant_id=tenant_id, referred_contact_id=referred.id, created_by=staff_name)
        db.add(row)

    row.source = source
    row.referrer_contact_id = referrer.id if referrer else None
    row.detail = (detail or "").strip()[:300] or None
    if source != PATIENT:
        row.reward_status = NONE
        row.earned_at = None
    elif _consent_signed(db, referred):
        row.reward_status = EARNED
        row.earned_at = row.earned_at or datetime.now(timezone.utc)
    else:
        row.reward_status = PENDING_CONSENT
        row.earned_at = None
    db.flush()
    return row


def mark_consent_signed(db: Session, referred: Contact) -> None:
    """Called when the referred patient signs: turns a pending reward into a
    reward the referrer has earned."""
    row = db.query(Referral).filter(Referral.referred_contact_id == referred.id).one_or_none()
    if row is not None and row.reward_status == PENDING_CONSENT:
        row.reward_status = EARNED
        row.earned_at = datetime.now(timezone.utc)


def apply_reward(db: Session, row: Referral, *, note: str, staff_name: str | None) -> Referral:
    if row.reward_status != EARNED:
        raise ReferralError("Solo se puede aplicar un descuento que ya fue ganado.")
    note = (note or "").strip()
    if not note:
        raise ReferralError("Escribe qué descuento se aplicó (por ejemplo: 10 % en la sesión del 20 de noviembre).")
    row.reward_status = APPLIED
    row.discount_note = note[:300]
    row.applied_at = datetime.now(timezone.utc)
    row.applied_by = staff_name
    return row