import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TenantMixin, TimestampMixin, UUIDPKMixin


class StaffRole(str, enum.Enum):
    """The two roles the client asked for: a secretary manages patients
    and appointments but never sees clinical content; a doctor sees
    everything a secretary does, plus clinical notes/recommendations.
    There is no "admin" role yet — accounts are created by a script
    (scripts/create_staff.py), not a self-service screen."""

    SECRETARY = "SECRETARY"
    DOCTOR = "DOCTOR"


class Staff(UUIDPKMixin, TenantMixin, TimestampMixin, Base):
    """A GRIP staff member who can log into the internal CRM screen
    (GET /crm). Entirely separate from Contact (patients) — a staff member
    is never a WhatsApp contact and vice versa."""

    __tablename__ = "staff"
    __table_args__ = (UniqueConstraint("tenant_id", "username", name="uq_staff_tenant_username"),)

    username: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[StaffRole] = mapped_column(Enum(StaffRole, name="staff_role"), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Staff {self.username} role={self.role}>"


class StaffSession(UUIDPKMixin, Base):
    """One row per logged-in device/browser. No TenantMixin — a session
    always belongs to exactly one staff member, and that staff member
    already carries the tenant. Only a SHA-256 hash of the actual cookie
    value is stored (see app.services.auth) — the point of a session
    table is precisely that a leaked database row can't be replayed as a
    working login."""

    __tablename__ = "staff_sessions"

    staff_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    staff: Mapped["Staff"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StaffSession staff_id={self.staff_id} expires_at={self.expires_at}>"