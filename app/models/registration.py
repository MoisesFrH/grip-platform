import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RegistrationForm(Base):
    """A filled-in "Registro de consulta inicial infantil". One row per form
    handed out in person; `form_type` (SENASA / PATRONATO / DIRECT) says which
    version was used and `answers` holds the responses keyed by the field keys
    of app/services/registration_forms.py. Sensitive (health data of minors):
    only reachable by logged-in staff of the same tenant."""

    __tablename__ = "registration_forms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    form_type: Mapped[str] = mapped_column(String(16), nullable=False)
    child_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    answers: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    filled_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )