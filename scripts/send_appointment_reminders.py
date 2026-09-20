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
from app.services import appointments

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run() -> int:
    db = SessionLocal()
    sent_count = 0
    try:
        tenants = db.query(Tenant).filter(Tenant.is_active.is_(True)).all()
        for tenant in tenants:
            due = appointments.appointments_needing_reminder(db, tenant)
            for appointment in due:
                contact = appointment.contact
                appointments.send_reminder(db, tenant, appointment, contact)
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