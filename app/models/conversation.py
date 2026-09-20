import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TenantMixin, TimestampMixin, UUIDPKMixin


class ConversationStatus(str, enum.Enum):
    """
    State machine from the architecture doc §11 (Conversation state machine).
    BOT_ACTIVE: the bot is handling messages automatically.
    WAITING_FOR_PATIENT: bot asked something and is waiting on the patient.
    WAITING_FOR_HUMAN: handed off, not yet claimed by an agent.
    HUMAN_ACTIVE: an agent has taken over; the bot must not reply.
    HUMAN_RESOLVED: agent marked it resolved; bot not yet resumed.
    BOT_RESUMED: control handed back to the bot after a human resolution.
    CLOSED: conversation archived (no activity expected).
    """

    BOT_ACTIVE = "BOT_ACTIVE"
    WAITING_FOR_PATIENT = "WAITING_FOR_PATIENT"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    HUMAN_ACTIVE = "HUMAN_ACTIVE"
    HUMAN_RESOLVED = "HUMAN_RESOLVED"
    BOT_RESUMED = "BOT_RESUMED"
    CLOSED = "CLOSED"


class ConversationChannel(str, enum.Enum):
    WHATSAPP = "WHATSAPP"


class Conversation(UUIDPKMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    channel: Mapped[ConversationChannel] = mapped_column(
        Enum(ConversationChannel, name="conversation_channel"),
        nullable=False,
        default=ConversationChannel.WHATSAPP,
    )
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, name="conversation_status"),
        nullable=False,
        default=ConversationStatus.BOT_ACTIVE,
        index=True,
    )

    assigned_to: Mapped[str | None] = mapped_column(
        String(120), nullable=True, comment="Human agent identifier, once assigned"
    )

    handoff_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    handoff_priority: Mapped[str | None] = mapped_column(String(16), nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Set once, the first time this conversation escalates (orchestrator._handoff)
    # and the first time an agent claims it (human_inbox.claim_conversation).
    # Neither can be reliably derived from updated_at (TimestampMixin bumps
    # that on ANY change to the row, not just these two), so they get their
    # own columns — this is what the "time to claim a critical case" metric
    # is computed from (see app.services.analytics.time_to_claim_critical).
    handoff_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    contact: Mapped["Contact"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", order_by="Message.created_at"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Conversation {self.id} status={self.status}>"