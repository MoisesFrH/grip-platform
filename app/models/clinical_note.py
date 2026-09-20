import enum
import uuid

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TenantMixin, TimestampMixin, UUIDPKMixin


class ClinicalNoteType(str, enum.Enum):
    RECOMMENDATION = "RECOMMENDATION"  # doctor's recommendation to the patient
    NOTE = "NOTE"  # doctor's internal note


class ClinicalNote(UUIDPKMixin, TenantMixin, TimestampMixin, Base):
    """
    A doctor's note or recommendation about a patient. This is the one
    genuinely clinical table in the schema, so it gets its own, tighter
    rules rather than reusing Contact/Appointment's:

      - Never read by the bot. Gemini's function-calling allowlist
        (app.services.tools) never includes a lookup against this table —
        clinical content must never reach the LLM, in either direction.
      - Never aggregated into analytics/dashboard (app.services.analytics
        only ever counts intents and statuses, never note content).
      - No structured "diagnosis" field, ICD code, or symptom checklist —
        content is a single free-text field the doctor writes, which is
        the minimum structure needed to be useful (data-minimization: we
        do not invent extra clinical fields just because a database
        *could* hold them).
      - `author_name` identifies the clinician who wrote it (free text for
        now, same reasoning as Appointment.therapist_name) for
        accountability — who wrote this note — not for any workflow logic.

    This table has no HTTP-exposed CRUD in this MVP; it exists so the data
    model has a real place for "doctor's notes and recommendations" per
    the client's request, ready to be wired to an internal staff-only
    screen (not WhatsApp, not the public dashboard) when that is built.
    """

    __tablename__ = "clinical_notes"

    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True, index=True
    )

    author_name: Mapped[str] = mapped_column(String(120), nullable=False)
    note_type: Mapped[ClinicalNoteType] = mapped_column(
        Enum(ClinicalNoteType, name="clinical_note_type"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    contact: Mapped["Contact"] = relationship()
    appointment: Mapped["Appointment | None"] = relationship(back_populates="notes")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ClinicalNote {self.id} type={self.note_type}>"