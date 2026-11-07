"""
GET /dashboard — the metrics view for GRIP staff (architecture doc §18:
logging/audit, expanded into an actual reporting surface).

The HTML shell is public (it is just a page with no data); every /dashboard/api
call requires a staff login, exactly like /crm — same session cookie — and
returns only the logged-in staff member's own tenant. The `tenant_slug` query
parameter is still accepted so older pages keep working, but it is ignored:
the tenant always comes from the session.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.routes.crm_deps import get_current_staff
from app.core.db import get_db
from app.models.staff import Staff
from app.models.tenant import Tenant
from app.services.analytics import dashboard_summary, patient_list, reminder_log

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _tenant_of(db: Session, staff: Staff) -> Tenant:
    return db.query(Tenant).filter(Tenant.id == staff.tenant_id).one()


@router.get("/api/summary")
def get_dashboard_summary(
    tenant_slug: str = "grip",  # ignored, kept for compatibility
    days: int = Query(default=30, ge=1, le=365),
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
) -> dict:
    return dashboard_summary(db, _tenant_of(db, staff), days=days)


@router.get("/api/reminders")
def get_reminder_log(
    tenant_slug: str = "grip",  # ignored, kept for compatibility
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
) -> dict:
    return {"reminders": reminder_log(db, _tenant_of(db, staff), days=days, limit=limit)}


@router.get("/api/patients")
def get_patient_list(
    tenant_slug: str = "grip",  # ignored, kept for compatibility
    limit: int = Query(default=200, ge=1, le=500),
    staff: Staff = Depends(get_current_staff),
    db: Session = Depends(get_db),
) -> dict:
    """Administrative contact list only — see patient_list's docstring.
    Not windowed by `days` like the other endpoints: a patient list is
    "everyone we know," not "everyone active in the last N days."."""
    return {"patients": patient_list(db, _tenant_of(db, staff), limit=limit)}