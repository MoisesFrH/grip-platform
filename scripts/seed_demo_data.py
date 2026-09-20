"""
Seeds a handful of realistic-looking demo patients/appointments for the
GRIP tenant, so the internal CRM screen (/crm) isn't empty during a demo.

Separate from scripts/seed_grip.py on purpose: that one seeds the
tenant's bot config (business info, therapists, services) and runs
automatically on every deploy (see the Procfile/deploy command) — it must
stay idempotent-and-safe-to-always-run. This script is meant to be run
once, by hand, whenever a fresh demo dataset is wanted, and is idempotent
by phone number: re-running it replaces each demo patient's appointments
and clinical notes rather than piling up duplicates, so it's safe to run
again before a second demo.

Usage (locally):
    .venv/bin/python -m scripts.seed_demo_data

On Railway (from your machine, with the Railway CLI linked to the project):
    railway run python -m scripts.seed_demo_data
"""

from datetime import datetime, timedelta, timezone

from app.core.db import SessionLocal
from app.models.appointment import Appointment, AppointmentStatus, ReminderResponse
from app.models.clinical_note import ClinicalNote, ClinicalNoteType
from app.models.contact import Contact, ContactStatus
from app.models.tenant import Tenant
from app.services import appointments as appointments_service


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_or_create_contact(db, tenant: Tenant, *, phone: str, name: str, status: ContactStatus) -> Contact:
    contact = (
        db.query(Contact)
        .filter(Contact.tenant_id == tenant.id, Contact.phone == phone)
        .one_or_none()
    )
    if contact is None:
        contact = Contact(tenant_id=tenant.id, phone=phone)
        db.add(contact)
    contact.name = name
    contact.status = status
    contact.last_interaction_at = _now()
    db.flush()
    return contact


def _reset_demo_history(db, contact: Contact) -> None:
    """Wipes this demo contact's OWN appointments/notes before reseeding,
    so re-running this script updates the demo instead of duplicating it.
    Scoped strictly to contact_id, so it never touches real patients or
    the separate test contacts used in the WhatsApp simulator."""
    db.query(ClinicalNote).filter(ClinicalNote.contact_id == contact.id).delete(synchronize_session=False)
    db.query(Appointment).filter(Appointment.contact_id == contact.id).delete(synchronize_session=False)
    db.flush()


def seed() -> None:
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.slug == "grip").one_or_none()
        if tenant is None:
            raise RuntimeError("Tenant 'grip' not found — run `python -m scripts.seed_grip` first.")

        # 1. Wanda Pérez — cita de mañana, recordatorio todavía sin enviar.
        wanda = _get_or_create_contact(
            db, tenant, phone="+18095550101", name="Wanda Pérez", status=ContactStatus.PATIENT
        )
        _reset_demo_history(db, wanda)
        appointments_service.create_appointment(
            db, tenant, wanda,
            therapist_name="María Fernanda Ruiz",
            scheduled_at=_now() + timedelta(hours=20),
        )

        # 2. Carlos Méndez — cita de mañana, recordatorio ya enviado y confirmado.
        carlos = _get_or_create_contact(
            db, tenant, phone="+18295550142", name="Carlos Méndez", status=ContactStatus.PATIENT
        )
        _reset_demo_history(db, carlos)
        apt_carlos = appointments_service.create_appointment(
            db, tenant, carlos,
            therapist_name="Andrés Salas",
            scheduled_at=_now() + timedelta(hours=26),
        )
        apt_carlos.reminder_sent_at = _now() - timedelta(hours=2)
        apt_carlos.reminder_response = ReminderResponse.CONFIRMED
        apt_carlos.status = AppointmentStatus.CONFIRMED

        # 3. Rosa Difo — recordatorio enviado, pidió reprogramar (para mostrar
        # el caso que siempre debe pasar a una persona del equipo).
        rosa = _get_or_create_contact(
            db, tenant, phone="+18495550177", name="Rosa Difo", status=ContactStatus.PATIENT
        )
        _reset_demo_history(db, rosa)
        apt_rosa = appointments_service.create_appointment(
            db, tenant, rosa,
            therapist_name="María Fernanda Ruiz",
            scheduled_at=_now() + timedelta(hours=18),
        )
        apt_rosa.reminder_sent_at = _now() - timedelta(hours=1)
        apt_rosa.reminder_response = ReminderResponse.CANCEL_OR_RESCHEDULE
        apt_rosa.status = AppointmentStatus.RESCHEDULE_REQUESTED

        # 4. Yolanda Ramírez — historial: una cita pasada ya completada (con
        # nota clínica) + una próxima cita agendada.
        yolanda = _get_or_create_contact(
            db, tenant, phone="+18495550110", name="Yolanda Ramírez", status=ContactStatus.PATIENT
        )
        _reset_demo_history(db, yolanda)
        apt_past = appointments_service.create_appointment(
            db, tenant, yolanda,
            therapist_name="Lucía Torres",
            scheduled_at=_now() - timedelta(days=7),
        )
        apt_past.status = AppointmentStatus.COMPLETED
        db.flush()
        db.add(ClinicalNote(
            tenant_id=tenant.id,
            contact_id=yolanda.id,
            appointment_id=apt_past.id,
            author_name="Lucía Torres",
            note_type=ClinicalNoteType.NOTE,
            content=(
                "Primera sesión de evaluación. Reporta ansiedad relacionada con el "
                "trabajo y dificultad para dormir. Se recomienda continuar con la "
                "batería de evaluación completa en las próximas dos sesiones."
            ),
        ))
        appointments_service.create_appointment(
            db, tenant, yolanda,
            therapist_name="Lucía Torres",
            scheduled_at=_now() + timedelta(days=3, hours=4),
        )

        # 5. Julián Objio — lead que escribió preguntando por precios, todavía
        # sin cita agendada (para mostrar ese estado también).
        _get_or_create_contact(
            db, tenant, phone="+18095550199", name="Julián Objio", status=ContactStatus.LEAD
        )

        db.commit()
        print(
            "Datos de demo listos: Wanda Pérez, Carlos Méndez, Rosa Difo, "
            "Yolanda Ramírez, Julián Objio."
        )
    finally:
        db.close()


if __name__ == "__main__":
    seed()