
"""
CRM routes for the in-person registration forms (Patronato / SeNaSa /
Privado). Mounted by crm.py under /crm. Staff-authenticated and scoped to the
staff member's tenant: these answers contain health data of minors.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.routes.crm_deps import _get_contact_or_404, get_current_staff
from app.core.db import get_db
from app.models.registration import RegistrationForm
from app.models.staff import Staff
from app.services import registration_forms as forms

router = APIRouter()


def _type_or_404(form_type: str) -> str:
    key = form_type.upper()
    key = forms.SLUGS.get(form_type.lower(), key)
    if key not in forms.FORM_TYPES:
        raise HTTPException(status_code=404, detail="Formulario no encontrado.")
    return key


class FormSummary(BaseModel):
    id: str
    form_type: str
    title: str
    child_name: str
    filled_by: str | None
    created_at: str


class FormDetail(FormSummary):
    contact_id: str | None
    answers: dict
    schema_: dict


class FormIn(BaseModel):
    form_type: str
    contact_id: str
    answers: dict


def _summary(row: RegistrationForm) -> dict:
    return {
        "id": str(row.id), "form_type": row.form_type, "title": forms.TITLES[row.form_type],
        "child_name": row.child_name, "filled_by": row.filled_by, "created_at": row.created_at.isoformat(),
    }


@router.get("/forms/schema/{form_type}")
def get_form_schema(form_type: str, staff: Staff = Depends(get_current_staff)) -> dict:
    return forms.get_schema(_type_or_404(form_type))


@router.post("/forms", response_model=FormSummary)
def create_form(body: FormIn, staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)):
    form_type = _type_or_404(body.form_type)
    contact = _get_contact_or_404(db, staff, body.contact_id)
    try:
        answers = forms.clean_answers(form_type, body.answers)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    row = RegistrationForm(
        tenant_id=staff.tenant_id, contact_id=contact.id, form_type=form_type,
        child_name=answers.get("child_name", ""), answers=answers, filled_by=staff.display_name,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _summary(row)


@router.get("/patients/{contact_id}/forms", response_model=list[FormSummary])
def list_patient_forms(contact_id: str, staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)):
    contact = _get_contact_or_404(db, staff, contact_id)
    rows = (
        db.query(RegistrationForm)
        .filter(RegistrationForm.tenant_id == staff.tenant_id, RegistrationForm.contact_id == contact.id)
        .order_by(RegistrationForm.created_at.desc())
        .all()
    )
    return [_summary(r) for r in rows]


@router.get("/forms/{form_id}")
def get_form(form_id: str, staff: Staff = Depends(get_current_staff), db: Session = Depends(get_db)) -> dict:
    try:
        form_uuid = uuid.UUID(form_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Registro no encontrado.")
    row = (
        db.query(RegistrationForm)
        .filter(RegistrationForm.id == form_uuid, RegistrationForm.tenant_id == staff.tenant_id)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Registro no encontrado.")
    return {
        **_summary(row),
        "contact_id": str(row.contact_id) if row.contact_id else None,
        "answers": row.answers,
        "schema": forms.get_schema(row.form_type),
    }