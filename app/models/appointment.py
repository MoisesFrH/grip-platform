import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TenantMixin, TimestampMixin, UUIDPKMixin


class AppointmentStatus(str, enum.Enum):
    """Lifecycle of a single scheduled appointment. Deliberately small and
    administrative — this is NOT a clinical record; no diagnosis, no
    treatment detail lives on this row (see ClinicalNote for that, kept in
    its own table with its own, tighter access rules)."""

    SCHEDULED = "SCHEDULED"
    CONFIRMED = "CONFIRMED"
    RESCHEDULE_REQUESTED = "RESCHEDULE_REQUESTED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    NO_SHOW = "NO_SHOW"


class ReminderResponse(str, enum.Enum):
    """What the patient's reply to the day-before reminder indicated.
    CANCEL_OR_RESCHEDULE is set the moment a reminder reply *looks like* a
    cancel/reschedule request, by a deterministic keyword check that runs
    before intent classification — never inferred by the bot's judgment
    alone (see app.services.appointments.check_reminder_response). Setting
    this always forces a human handoff; it is not, by itself, enough to
    change AppointmentStatus, since only a human confirms what actually
    happens to the appointment."""

    CONFIRMED = "CONFIRMED"
    CANCEL_OR_RESCHEDULE = "CANCEL_OR_RESCHEDULE"


class Appointment(UUIDPKMixin, TenantMixin, TimestampMixin, Base):
    """
    Minimal appointment record: who, with which therapist, when, and its
    status. Deliberately does not store why the patient is being seen —
    that already belongs to ClinicalNote, so a query that only needs
    scheduling data (e.g. the reminder job) never touches clinical
    content at all.
    """

    __tablename__ = "appointments"

    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Free-text for now (matches how therapists are stored in Tenant.config
    # today — there is no separate therapists table yet). Promote to a FK
    # once therapists get their own table.
    therapist_name: Mapped[str] = mapped_column(String(120), nullable=False)

    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus, name="appointment_status"),
        nullable=False,
        default=AppointmentStatus.SCHEDULED,
        index=True,
    )

    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminder_response: Mapped[ReminderResponse | None] = mapped_column(
        Enum(ReminderResponse, name="appointment_reminder_response"), nullable=True
    )

    contact: Mapped["Contact"] = relationship()
    notes: Mapped[list["ClinicalNote"]] = relationship(back_populates="appointment")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Appointment {self.id} {self.scheduled_at} status={self.status}>"