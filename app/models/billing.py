import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

PAYMENT_METHODS = ("TRANSFER", "CASH", "CARD", "SENASA", "PATRONATO", "OTHER")
PAYMENT_STATUSES = ("PENDING", "REPORTED", "VERIFIED", "REJECTED")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PriceItem(Base):
    """A line of the clinic's price list. Amount is NULL until an
    administrator fills it in. Lives in its own table (not Tenant.config)
    because scripts/seed_grip.py overwrites the config on every deploy."""

    __tablename__ = "price_items"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_price_item_tenant_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class Payment(Base):
    """One payment record per appointment. Status flow:
    PENDING (registered, nothing received) -> REPORTED (patient says they
    paid) -> VERIFIED (staff saw the money) or REJECTED. Plain strings, not
    database enums, so adding a method later needs no migration."""

    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("appointment_id", name="uq_payment_appointment"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    appointment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    method: Mapped[str] = mapped_column(String(16), nullable=False, default="TRANSFER")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)