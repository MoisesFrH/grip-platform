"""
GET /dashboard — the metrics view for GRIP staff (architecture doc §18:
logging/audit, expanded into an actual reporting surface). Dev-only for
now, same as /simulator: no auth yet, gated behind APP_ENV != "production"
in app.main until a real login exists.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.tenant import Tenant
from app.services.analytics import dashboard_summary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/api/summary")
def get_dashboard_summary(
    tenant_slug: str = "grip",
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> dict:
    tenant = db.query(Tenant).filter(Tenant.slug == tenant_slug).one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"Unknown tenant slug: {tenant_slug}")
    return dashboard_summary(db, tenant, days=days)