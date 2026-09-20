"""
GET /dashboard — the metrics view for GRIP staff (architecture doc §18:
logging/audit, expanded into an actual reporting surface). Registered
unconditionally in app.main (unlike /dev/chat and /simulator, which are
dev-only) — this is a real feature GRIP staff use ongoing. It has no auth
yet either way; that's a separate, explicitly-tracked gap.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.tenant import Tenant
from app.services.analytics import dashboard_summary, reminder_log

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _get_tenant(db: Session, tenant_slug: str) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.slug == tenant_slug).one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"Unknown tenant slug: {tenant_slug}")
    return tenant


@router.get("/api/summary")
def get_dashboard_summary(
    tenant_slug: str = "grip",
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> dict:
    tenant = _get_tenant(db, tenant_slug)
    return dashboard_summary(db, tenant, days=days)


@router.get("/api/reminders")
def get_reminder_log(
    tenant_slug: str = "grip",
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    tenant = _get_tenant(db, tenant_slug)
    return {"reminders": reminder_log(db, tenant, days=days, limit=limit)}