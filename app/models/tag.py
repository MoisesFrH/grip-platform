import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TenantMixin, UUIDPKMixin

# Default taxonomy from the architecture doc §11. Tenants may extend this
# with their own tags via TENANT_CONFIG; this list is the starting set
# seeded for every new tenant, not a hard-coded enum, since a restaurant
# or hotel tenant will need a different vocabulary.
DEFAULT_TAGS = [
    "NEW_CONTACT",
    "PATIENT",
    "LEAD",
    "BOOKING",
    "APPOINTMENT_CHANGE",
    "APPOINTMENT_CANCEL",
    "PAYMENT",
    "EVALUATION",
    "THERAPIST",
    "CLINICAL",
    "HUMAN_HANDOFF",
    "SAFETY_RISK",
]


class Tag(UUIDPKMixin, TenantMixin, Base):
    """A tenant-scoped label. Tags are business-vocabulary, not enums, so a
    tenant can add its own (e.g. a restaurant's `LARGE_PARTY`) without a
    migration."""

    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_tag_tenant_name"),)

    name: Mapped[str] = mapped_column(String(64), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tag {self.name}>"


class ContactTag(Base):
    """Join table: which tags apply to which contact, and when they were applied."""

    __tablename__ = "contact_tags"

    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    contact: Mapped["Contact"] = relationship(back_populates="tags")
    tag: Mapped["Tag"] = relationship()
