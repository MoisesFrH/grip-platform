import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ConsentRecord(Base):
    """Informed consent of one patient — signed once, before treatment starts.

    One row per patient. While unsigned, `token` is the secret in the QR/link
    the family scans (regenerating the link replaces it). When signed we keep a
    snapshot of the exact text that was shown (`text_snapshot`) next to the
    version, who signed, their relationship to the child, the drawn signature
    (PNG bytes) and the timestamp, so a later wording change never rewrites
    what someone actually agreed to."""

    __tablename__ = "consent_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signer_relation: Mapped[str | None] = mapped_column(String(80), nullable=True)
    child_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    text_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    text_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    signature_png: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)