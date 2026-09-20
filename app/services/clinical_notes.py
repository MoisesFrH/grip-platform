"""
Minimal write/read helpers for ClinicalNote. Deliberately not imported by
orchestrator.py, gemini_client.py or tools.py — the bot must never read or
write clinical notes. This module exists for a future internal,
staff-authenticated screen, not for any WhatsApp-facing or dashboard code
path.
"""

from sqlalchemy.orm import Session

from app.models.clinical_note import ClinicalNote, ClinicalNoteType
from app.models.contact import Contact
from app.models.tenant import Tenant


def add_clinical_note(
    db: Session,
    tenant: Tenant,
    contact: Contact,
    *,
    author_name: str,
    note_type: ClinicalNoteType,
    content: str,
    appointment_id=None,
) -> ClinicalNote:
    note = ClinicalNote(
        tenant_id=tenant.id,
        contact_id=contact.id,
        appointment_id=appointment_id,
        author_name=author_name,
        note_type=note_type,
        content=content,
    )
    db.add(note)
    db.flush()
    return note


def list_notes_for_contact(db: Session, tenant: Tenant, contact: Contact) -> list[ClinicalNote]:
    return (
        db.query(ClinicalNote)
        .filter(ClinicalNote.tenant_id == tenant.id, ClinicalNote.contact_id == contact.id)
        .order_by(ClinicalNote.created_at.desc())
        .all()
    )