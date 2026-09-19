import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TenantMixin, TimestampMixin, UUIDPKMixin


class MessageDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageSender(str, enum.Enum):
    PATIENT = "patient"
    BOT = "bot"
    HUMAN_AGENT = "human_agent"
    SYSTEM = "system"


class MessageStatus(str, enum.Enum):
    """Mirrors Twilio's MessageStatus callback values for outbound messages."""

    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    UNDELIVERED = "undelivered"
    RECEIVED = "received"  # inbound messages land here; no delivery lifecycle applies


class Message(UUIDPKMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        # Twilio may retry a webhook delivery; this is what makes reprocessing
        # a no-op instead of a duplicate message (architecture doc §4, step 2).
        UniqueConstraint("twilio_message_id", name="uq_message_twilio_id"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    direction: Mapped[MessageDirection] = mapped_column(
        Enum(MessageDirection, name="message_direction"), nullable=False
    )
    sender: Mapped[MessageSender] = mapped_column(Enum(MessageSender, name="message_sender"), nullable=False)

    body: Mapped[str | None] = mapped_column(String, nullable=True)

    # Nullable: only inbound messages are classified, and not every outbound
    # message corresponds 1:1 to a single intent (e.g. clarifying questions).
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    intent_confidence: Mapped[float | None] = mapped_column(nullable=True)

    # Twilio's own message SID (e.g. "SM...") — the idempotency key and the
    # id used to correlate delivery-status callbacks.
    twilio_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus, name="message_status"), nullable=False, default=MessageStatus.RECEIVED
    )

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Free-form: tool calls made, template name used, error detail, AI cost
    # estimate, etc. Kept schemaless here; promoted to columns/tables once a
    # field needs to be queried or reported on (architecture doc §18).
    extra_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Message {self.id} {self.direction} intent={self.intent}>"
