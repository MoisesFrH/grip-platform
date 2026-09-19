from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPKMixin


class Tenant(UUIDPKMixin, TimestampMixin, Base):
    """
    A tenant is one client business on the platform (GRIP, Centro B, a
    restaurant, ...). Everything else — contacts, conversations, messages,
    services, therapists, safety protocol, AI settings — is scoped to a
    tenant_id.

    `config` holds the full TENANT_CONFIG blob described in the architecture
    doc §17 (business_name, timezone, language, opening hours, services,
    therapists, booking instructions, safety protocol, AI settings,
    branding, ...). It starts as a single JSON column for the MVP so the
    shape can evolve without a migration on every tweak; sections that turn
    out to need querying or validation (e.g. therapists, safety protocol)
    get promoted to their own tables in a later phase.
    """

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(60), unique=True, nullable=False, index=True)

    whatsapp_number: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)

    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="es")

    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tenant {self.slug}>"
