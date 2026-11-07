"""
Public consent-signing routes, reached from the QR code / link that staff
generate in the CRM. No login: the unguessable token in the URL is the
credential, it only ever reaches one patient's consent, and it only allows
reading the consent text and signing once.

Mount in app/main.py:  app.include_router(consent_public_router)
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.contact import Contact
from app.models.tenant import Tenant
from app.services import consent

router = APIRouter(tags=["consent"])

_PAGE = Path(__file__).resolve().parent.parent.parent / "static" / "consentimiento.html"


class SignIn(BaseModel):
    signer_name: str
    relation: str
    child_name: str
    accepted: bool
    signature: str


def _row_or_404(db: Session, token: str):
    row = consent.get_by_token(db, token)
    if row is None:
        raise HTTPException(status_code=404, detail="Este enlace no es válido. Pide uno nuevo en la recepción.")
    return row


@router.get("/consentimiento/{token}")
def consent_page(token: str) -> FileResponse:
    # Same page for any token; the page asks the API, which answers 404 for a bad one.
    return FileResponse(_PAGE, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/consentimiento/api/{token}")
def consent_info(token: str, db: Session = Depends(get_db)) -> dict:
    row = _row_or_404(db, token)
    tenant = db.get(Tenant, row.tenant_id)
    contact = db.get(Contact, row.contact_id)
    return {
        "clinic_name": getattr(tenant, "name", None) or "ND Neurodiversa",
        "signed": row.signed_at is not None,
        "child_name": (contact.name if contact and contact.name else ""),
        "title": consent.CONSENT_TITLE,
        "paragraphs": [{"title": t, "body": b} for t, b in consent.CONSENT_PARAGRAPHS],
        "relations": consent.RELATIONS,
        "version": consent.CONSENT_VERSION,
    }


@router.post("/consentimiento/api/{token}/firmar")
def sign_consent(token: str, body: SignIn, db: Session = Depends(get_db)) -> dict:
    row = _row_or_404(db, token)
    try:
        consent.sign(
            db, row,
            signer_name=body.signer_name, relation=body.relation, child_name=body.child_name,
            accepted=body.accepted, signature_data_url=body.signature,
        )
    except consent.ConsentError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    return {"ok": True}