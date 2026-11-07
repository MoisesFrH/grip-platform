"""
Staff side of the consent (mounted by crm.py under /crm): generate the QR /
link, see whether a patient signed, and view the signed document.
"""

import io
import os

import segno
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.routes.crm_deps import _get_contact_or_404, get_current_staff
from app.core.db import get_db
from app.models.staff import Staff
from app.services import consent

router = APIRouter()


def _base_url(request: Request) -> str:
    configured = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc)).split(",")[0].strip()
    return f"{scheme}://{host}"


def _status(request: Request, row) -> dict:
    if row is None:
        return {"status": "NONE"}
    if row.signed_at is not None:
        return {
            "status": "SIGNED",
            "signed_at": row.signed_at.isoformat(),
            "signer_name": row.signer_name,
            "signer_relation": row.signer_relation,
            "child_name": row.child_name,
            "version": row.text_version,
            "text": row.text_snapshot,
        }
    return {"status": "PENDING", "url": f"{_base_url(request)}/consentimiento/{row.token}"}


@router.get("/patients/{contact_id}/consent")
def get_consent(contact_id: str, request: Request, staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)) -> dict:
    contact = _get_contact_or_404(db, staff, contact_id)
    return _status(request, consent.get_record(db, contact))


@router.post("/patients/{contact_id}/consent/link")
def create_consent_link(contact_id: str, request: Request, staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)) -> dict:
    contact = _get_contact_or_404(db, staff, contact_id)
    try:
        row = consent.create_or_renew_link(db, contact, staff.display_name)
    except consent.ConsentError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return _status(request, row)


@router.get("/patients/{contact_id}/consent/qr.svg")
def consent_qr(contact_id: str, request: Request, staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)) -> Response:
    contact = _get_contact_or_404(db, staff, contact_id)
    row = consent.get_record(db, contact)
    if row is None or row.signed_at is not None:
        raise HTTPException(status_code=404, detail="No hay un enlace de firma pendiente.")
    buffer = io.BytesIO()
    segno.make(f"{_base_url(request)}/consentimiento/{row.token}", error="m").save(
        buffer, kind="svg", scale=10, border=2, dark="#1d3375", light="#ffffff"
    )
    return Response(buffer.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@router.get("/patients/{contact_id}/consent/signature.png")
def consent_signature(contact_id: str, staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)) -> Response:
    contact = _get_contact_or_404(db, staff, contact_id)
    row = consent.get_record(db, contact)
    if row is None or row.signature_png is None:
        raise HTTPException(status_code=404, detail="Sin firma.")
    return Response(row.signature_png, media_type="image/png", headers={"Cache-Control": "no-store"})