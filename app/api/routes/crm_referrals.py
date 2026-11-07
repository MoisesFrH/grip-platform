"""Referral tracking for the CRM (mounted by crm.py under /crm)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.routes.crm_deps import _get_contact_or_404, get_current_staff
from app.core.db import get_db
from app.models.contact import Contact
from app.models.referral import Referral
from app.models.staff import Staff
from app.services import referrals

router = APIRouter()


class ReferralIn(BaseModel):
    source: str
    referrer_contact_id: str | None = None
    detail: str | None = None


class ApplyIn(BaseModel):
    note: str


def _person(contact: Contact | None) -> dict | None:
    return None if contact is None else {"id": str(contact.id), "name": contact.name, "phone": contact.phone}


def _row(db: Session, r: Referral, *, show: str) -> dict:
    other_id = r.referrer_contact_id if show == "referrer" else r.referred_contact_id
    other = db.get(Contact, other_id) if other_id else None
    return {
        "id": str(r.id),
        "source": r.source,
        "source_label": referrals.SOURCES.get(r.source, r.source),
        "detail": r.detail,
        "person": _person(other),
        "reward_status": r.reward_status,
        "reward_label": referrals.REWARD_LABELS.get(r.reward_status, r.reward_status),
        "discount_note": r.discount_note,
        "earned_at": r.earned_at.isoformat() if r.earned_at else None,
        "applied_at": r.applied_at.isoformat() if r.applied_at else None,
        "applied_by": r.applied_by,
    }


@router.get("/patients/{contact_id}/referrals")
def get_referrals(contact_id: str, staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)) -> dict:
    contact = _get_contact_or_404(db, staff, contact_id)
    received = db.query(Referral).filter(Referral.referred_contact_id == contact.id).one_or_none()
    made = (
        db.query(Referral)
        .filter(Referral.tenant_id == staff.tenant_id, Referral.referrer_contact_id == contact.id)
        .order_by(Referral.created_at.desc())
        .all()
    )
    return {
        "sources": [{"key": k, "label": v} for k, v in referrals.SOURCES.items()],
        "received": _row(db, received, show="referrer") if received else None,
        "made": [_row(db, r, show="referred") for r in made],
        "earned_count": sum(1 for r in made if r.reward_status == referrals.EARNED),
    }


@router.put("/patients/{contact_id}/referral")
def put_referral(contact_id: str, body: ReferralIn, staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)) -> dict:
    contact = _get_contact_or_404(db, staff, contact_id)
    referrer = _get_contact_or_404(db, staff, body.referrer_contact_id) if body.referrer_contact_id else None
    try:
        row = referrals.set_referral(
            db, tenant_id=staff.tenant_id, referred=contact, source=body.source,
            referrer=referrer, detail=body.detail, staff_name=staff.display_name,
        )
    except referrals.ReferralError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    return _row(db, row, show="referrer")


@router.post("/referrals/{referral_id}/apply")
def apply_referral(referral_id: str, body: ApplyIn, staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)) -> dict:
    try:
        rid = uuid.UUID(referral_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Referencia no encontrada.")
    row = db.query(Referral).filter(Referral.id == rid, Referral.tenant_id == staff.tenant_id).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Referencia no encontrada.")
    try:
        referrals.apply_reward(db, row, note=body.note, staff_name=staff.display_name)
    except referrals.ReferralError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    return _row(db, row, show="referred")