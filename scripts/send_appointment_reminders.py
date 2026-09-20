"""
Sends the day-before appointment reminder to every patient with an
appointment tomorrow (see app.services.appointments.appointments_needing_reminder
for the exact window). Meant to run once a day — on Railway, add this as a
Cron Job/Scheduled Job pointing at:

    python -m scripts.send_appointment_reminders

Idempotent: an appointment only gets a reminder once
(Appointment.reminder_sent_at is set immediately after sending), so
running this twice in a day, or retrying after a crash, never double-sends.

Usage:
    .venv/bin/python -m scripts.send_appointment_reminders
"""

import logging

from app.core.db import SessionLocal
from app.models.tenant import Tenant
from app.services import appointments, crm
from app.services.messaging import send_whatsapp_message
from app.models.message import MessageSender

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _format_when(scheduled_at) -> str:
    # Spanish, no year (it's always "tomorrow"): "a las 3:00 p. m."
    return scheduled_at.strftime("a las %I:%M %p").lower().replace("am", "a. m.").replace("pm", "p. m.")


def run() -> int:
    db = SessionLocal()
    sent_count = 0
    try:
        tenants = db.query(Tenant).filter(Tenant.is_active.is_(True)).all()
        for tenant in tenants:
            due = appointments.appointments_needing_reminder(db, tenant)
            for appointment in due:
                contact = appointment.contact
                name_suffix = f" {contact.name}" if contact.name else ""
                body = appointments.APPOINTMENT_REMINDER_TEMPLATE.format(
                    name_suffix=name_suffix,
                    when=_format_when(appointment.scheduled_at),
                    therapist=appointment.therapist_name,
                )

                conversation = crm.get_or_create_open_conversation(db, tenant, contact)
                crm.record_outbound_message(db, conversation, body, sender=MessageSender.SYSTEM)
                appointments.mark_reminder_sent(appointment)
                send_whatsapp_message(contact.phone, body)

                sent_count += 1
                logger.info("Reminder sent: tenant=%s contact=%s appointment=%s", tenant.slug, contact.phone, appointment.id)

            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    logger.info("Done. %d reminder(s) sent.", sent_count)
    return sent_count


if __name__ == "__main__":
    run()