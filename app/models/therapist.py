import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Therapist(Base):
    """A person who sees patients; the columns of the agenda. Appointments
    still store the therapist's NAME as text (Appointment.therapist_name), so
    this table is only the list the CRM offers — and, deliberately, it is not
    part of Tenant.config because scripts/seed_grip.py overwrites that on
    every deploy."""

    __tablename__ = "therapists"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_therapist_tenant_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )