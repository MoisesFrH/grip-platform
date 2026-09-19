import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TenantMixin, TimestampMixin, UUIDPKMixin


class ContactStatus(str, enum.Enum):
    """Coarse lifecycle status, independent of the tag taxonomy (§11)."""

    NEW_CONTACT = "NEW_CONTACT"
    LEAD = "LEAD"
    PATIENT = "PATIENT"
    INACTIVE = "INACTIVE"


class Contact(UUIDPKMixin, TenantMixin, TimestampMixin, Base):
    """
    A person who has messaged a tenant's WhatsApp number. One contact per
    (tenant_id, phone) — the same phone number under two different tenants
    is two different contacts, since they are unrelated businesses.
    """

    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("tenant_id", "phone", name="uq_contact_tenant_phone"),)

    phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    whatsapp_profile_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[ContactStatus] = mapped_column(
        Enum(ContactStatus, name="contact_status"), nullable=False, default=ContactStatus.NEW_CONTACT
    )

    last_interaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversations: Mapped[list["Conversation"]] = relationship(back_populates="contact")
    tags: Mapped[list["ContactTag"]] = relationship(back_populates="contact", cascade="all, delete-orphan")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Contact {self.phone} tenant={self.tenant_id}>"
