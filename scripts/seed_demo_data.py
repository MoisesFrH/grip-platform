"""
Siembra el dataset de demo de ND Neurodiversa (tenant slug "grip"): siete
pacientes ficticios con historial largo (muchas sesiones, notas clínicas
extensas, citas pasadas y próximas, y una conversación de WhatsApp cada uno),
para que el CRM (/crm) y el dashboard (/dashboard) se vean completos en una
demo. Los datos viven en scripts/demo_patients.py.

Separado de scripts/seed_grip.py a propósito: aquel siembra la configuración
del bot y corre en cada deploy; este se corre a mano, solo cuando se quiere
refrescar la demo. Es idempotente por teléfono: volver a correrlo reemplaza
las citas, conversaciones y notas de cada contacto de demo.

También elimina los contactos de demo de versiones anteriores de este script
(por teléfono, todos ficticios). Los contactos de prueba sin nombre del
simulador no se tocan salvo que pidas --wipe-others, que borra TODO contacto
del centro que no sea de la demo (con confirmación). Úsalo solo en una base
de demo, nunca en una base con pacientes reales.

Un caso deliberadamente ausente: no hay ejemplos de riesgo suicida ni de
autolesión. El protocolo de seguridad se demuestra en vivo en el simulador,
con las palabras que tú elijas.

Uso (local):
    .venv/bin/python -m scripts.seed_demo_data
    .venv/bin/python -m scripts.seed_demo_data --wipe-others

En Railway (desde tu máquina, con el túnel a la base):
    python -m scripts.seed_demo_data --wipe-others
"""

import argparse
from datetime import datetime, timedelta, timezone

from app.core.db import SessionLocal
from app.models.appointment import Appointment, AppointmentStatus, ReminderResponse
from app.models.clinical_note import ClinicalNote, ClinicalNoteType
from app.models.contact import Contact, ContactStatus
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message, MessageDirection, MessageSender, MessageStatus
from app.models.tenant import Tenant
from app.services import appointments as appointments_service
from scripts.demo_patients import PATIENTS


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_or_create_contact(
    db, tenant: Tenant, *, phone: str, name: str, status: ContactStatus, backdate_created_days: int | None = None
) -> Contact:
    contact = (
        db.query(Contact)
        .filter(Contact.tenant_id == tenant.id, Contact.phone == phone)
        .one_or_none()
    )
    is_new = contact is None
    if contact is None:
        contact = Contact(tenant_id=tenant.id, phone=phone)
        db.add(contact)
    contact.name = name
    contact.status = status
    contact.last_interaction_at = _now()
    if is_new and backdate_created_days:
        # Makes this contact read as an existing/"returning" patient rather
        # than someone who just messaged for the first time this week —
        # see new_vs_returning_patients in app/services/analytics.py, which
        # compares Contact.created_at to the reporting window's start.
        contact.created_at = _now() - timedelta(days=backdate_created_days)
    db.flush()
    return contact


def _reset_demo_history(db, contact: Contact) -> None:
    """Wipes this demo contact's OWN appointments/notes/conversations
    before reseeding, so re-running this script updates the demo instead
    of duplicating it. Scoped strictly to contact_id, so it never touches
    real patients or the separate test contacts used in the WhatsApp
    simulator. Deleting a Conversation cascades to its Messages at the
    database level (see Message.conversation_id's ondelete="CASCADE")."""
    db.query(ClinicalNote).filter(ClinicalNote.contact_id == contact.id).delete(synchronize_session=False)
    db.query(Appointment).filter(Appointment.contact_id == contact.id).delete(synchronize_session=False)
    db.query(Conversation).filter(Conversation.contact_id == contact.id).delete(synchronize_session=False)
    db.flush()


def _add_note(
    db,
    tenant: Tenant,
    contact: Contact,
    *,
    days_ago: float,
    author: str,
    content: str,
    note_type: ClinicalNoteType = ClinicalNoteType.NOTE,
    appointment: Appointment | None = None,
) -> ClinicalNote:
    """Adds one fictional clinical note, dated `days_ago` days back so the
    patient's history reads like real follow-up over several weeks."""
    note = ClinicalNote(
        tenant_id=tenant.id,
        contact_id=contact.id,
        appointment_id=appointment.id if appointment else None,
        author_name=author,
        note_type=note_type,
        content=content,
    )
    # Hora de consulta plausible (10:00 a 16:59) en vez de la hora a la que se corrió el seed.
    when = _now() - timedelta(days=days_ago)
    note.created_at = when.replace(hour=14 + len(content) % 7, minute=(len(content) * 7) % 60, second=0, microsecond=0)
    db.add(note)
    return note


def _seed_conversation(
    db,
    tenant: Tenant,
    contact: Contact,
    *,
    days_ago: float,
    hour: int,
    minute: int = 0,
    status: ConversationStatus,
    turns: list[tuple[MessageDirection, MessageSender, str, str | None]],
    handoff_reason: str | None = None,
    handoff_priority: str | None = None,
    triggered_after_minutes: float = 3,
    claimed_after_minutes: float | None = None,
    safety_categories: dict[int, str] | None = None,
) -> Conversation:
    """turns: (direction, sender, body, intent) tuples, in order — intent
    is only meaningful on inbound turns (matches how the real bot only
    classifies inbound messages). safety_categories maps a turn index to
    a SafetyCategory value, stored in that message's extra_metadata the
    same way app.services.safety/orchestrator does it for real."""
    started_at = (_now() - timedelta(days=days_ago)).replace(hour=hour, minute=minute, second=0, microsecond=0)

    conversation = Conversation(
        tenant_id=tenant.id, contact_id=contact.id, status=status, started_at=started_at
    )
    db.add(conversation)
    db.flush()

    t = started_at
    for i, (direction, sender, body, intent) in enumerate(turns):
        message = Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            direction=direction,
            sender=sender,
            body=body,
            intent=intent,
            status=MessageStatus.RECEIVED if direction == MessageDirection.INBOUND else MessageStatus.DELIVERED,
            sent_at=t if direction == MessageDirection.OUTBOUND else None,
            created_at=t,
            extra_metadata={"safety_category": safety_categories[i]} if safety_categories and i in safety_categories else {},
        )
        db.add(message)
        t += timedelta(minutes=2)

    conversation.last_message_at = t - timedelta(minutes=2)

    if handoff_reason:
        conversation.handoff_reason = handoff_reason
        conversation.handoff_priority = handoff_priority
        conversation.handoff_triggered_at = started_at + timedelta(minutes=triggered_after_minutes)
        if claimed_after_minutes is not None:
            conversation.claimed_at = conversation.handoff_triggered_at + timedelta(minutes=claimed_after_minutes)

    db.flush()
    return conversation


# Teléfonos de versiones anteriores de la demo (todos ficticios): se borran
# siempre, para que no queden mezclados con el dataset nuevo.
LEGACY_DEMO_PHONES = [
    "+18095550101", "+18295550142", "+18495550177", "+18495550110", "+18095550199",
    "+18095550210", "+18295550233", "+18495550256", "+18095550278",
    "+18095550311", "+18295550322", "+18495550333", "+18095550344", "+18295550355",
    "+18495550366", "+18095550377", "+18295550388", "+18495550399", "+18095550410",
]

IN, OUT = MessageDirection.INBOUND, MessageDirection.OUTBOUND
_DIRECTIONS = {"IN": IN, "OUT": OUT}
_SENDERS = {
    "PATIENT": MessageSender.PATIENT,
    "BOT": MessageSender.BOT,
    "HUMAN": MessageSender.HUMAN_AGENT,
    "SYSTEM": MessageSender.SYSTEM,
}
_NOTE_TYPES = {"N": ClinicalNoteType.NOTE, "R": ClinicalNoteType.RECOMMENDATION}
_REMINDER = {
    "CONFIRMED": (ReminderResponse.CONFIRMED, AppointmentStatus.CONFIRMED),
    "CANCEL_OR_RESCHEDULE": (ReminderResponse.CANCEL_OR_RESCHEDULE, AppointmentStatus.RESCHEDULE_REQUESTED),
}


def _delete_contact(db, contact: Contact) -> None:
    _reset_demo_history(db, contact)
    db.query(Contact).filter(Contact.id == contact.id).delete(synchronize_session=False)


def _seed_patient(db, tenant: Tenant, spec: dict, index: int) -> Contact:
    contact = _get_or_create_contact(
        db, tenant, phone=spec["phone"], name=spec["name"], status=ContactStatus.PATIENT,
        backdate_created_days=spec["backdate_days"],
    )
    _reset_demo_history(db, contact)
    therapist = spec["therapist"]

    # Citas pasadas: i = 0 es la más reciente. Hora fija por paciente, para
    # que el historial se vea como sesiones agendadas y no como instantes sueltos.
    sessions = spec["sessions"]
    hour_utc = 13 + index  # 9:00 a 15:00 en República Dominicana
    for i in range(sessions["count"]):
        days_ago = sessions["last_days_ago"] + sessions["step_days"] * i
        when = (_now() - timedelta(days=days_ago)).replace(hour=hour_utc, minute=0, second=0, microsecond=0)
        appt = appointments_service.create_appointment(db, tenant, contact, therapist_name=therapist, scheduled_at=when)
        appt.status = AppointmentStatus.NO_SHOW if i in sessions["no_show_idx"] else AppointmentStatus.COMPLETED

    # Próximas citas; si traen recordatorio, queda ya enviado y respondido.
    for up in spec["upcoming"]:
        appt = appointments_service.create_appointment(
            db, tenant, contact, therapist_name=therapist, scheduled_at=_now() + timedelta(days=up["days"])
        )
        if up.get("reminder"):
            response, status = _REMINDER[up["reminder"]]
            appt.reminder_sent_at = _now() - timedelta(hours=1)
            appt.reminder_response = response
            appt.status = status

    for days_ago, author, kind, content in spec["notes"]:
        _add_note(db, tenant, contact, days_ago=days_ago, author=author, note_type=_NOTE_TYPES[kind], content=content)

    conv = spec["conversation"]
    hour = conv["hour"] if conv["hour"] is not None else (_now() - timedelta(days=conv["days_ago"])).hour
    _seed_conversation(
        db, tenant, contact,
        days_ago=conv["days_ago"], hour=hour, status=ConversationStatus[conv["status"]],
        turns=[(_DIRECTIONS[d], _SENDERS[snd], body, intent) for d, snd, body, intent in conv["turns"]],
        handoff_reason=conv.get("handoff_reason"),
        handoff_priority=conv.get("handoff_priority"),
        triggered_after_minutes=conv.get("triggered_after_minutes", 3),
        claimed_after_minutes=conv.get("claimed_after_minutes"),
        safety_categories=conv.get("safety_categories"),
    )
    return contact


def seed(wipe_others: bool = False, assume_yes: bool = False) -> None:
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.slug == "grip").one_or_none()
        if tenant is None:
            raise RuntimeError("Tenant 'grip' not found — run `python -m scripts.seed_grip` first.")

        demo_phones = {spec["phone"] for spec in PATIENTS}

        # Versiones anteriores de la demo (solo los teléfonos ficticios de la lista).
        legacy = (
            db.query(Contact)
            .filter(Contact.tenant_id == tenant.id, Contact.phone.in_(set(LEGACY_DEMO_PHONES) - demo_phones))
            .all()
        )
        for contact in legacy:
            _delete_contact(db, contact)

        for index, spec in enumerate(PATIENTS):
            _seed_patient(db, tenant, spec, index)

        removed_others = 0
        if wipe_others:
            others = (
                db.query(Contact)
                .filter(Contact.tenant_id == tenant.id, Contact.phone.notin_(demo_phones))
                .all()
            )
            if others and not assume_yes:
                print(f"Se van a BORRAR {len(others)} contactos que no son de la demo, con sus citas, notas y conversaciones.")
                print("Ejemplos:", ", ".join((c.name or "(sin nombre)") + " " + c.phone for c in others[:5]))
                if input("Escribe BORRAR para confirmar: ").strip() != "BORRAR":
                    db.rollback()
                    print("Cancelado. No se cambió nada.")
                    return
            for contact in others:
                _delete_contact(db, contact)
            removed_others = len(others)

        db.commit()
        print(
            f"Datos de demo listos: {len(PATIENTS)} pacientes ("
            + ", ".join(spec["name"] for spec in PATIENTS)
            + f"). Contactos de demo antiguos eliminados: {len(legacy)}."
            + (f" Otros contactos eliminados: {removed_others}." if wipe_others else "")
        )
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Siembra el dataset de demo de ND Neurodiversa.")
    parser.add_argument("--wipe-others", action="store_true", help="Borra todo contacto del centro que no sea de la demo (pide confirmación).")
    parser.add_argument("--yes", action="store_true", help="Con --wipe-others, no pide confirmación.")
    args = parser.parse_args()
    seed(wipe_others=args.wipe_others, assume_yes=args.yes)