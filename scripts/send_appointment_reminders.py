"""
Sends the automatic appointment reminders: one 48 hours before and one 24
hours before each appointment (see app.services.appointments.reminders_due for
the exact windows). Each message carries a one-sentence summary of what will be
worked on (from the CRM notes) and asks whether the family wants to pay in
advance by transfer.

Meant to run HOURLY. On Railway: add a Cron Job/Scheduled Job with schedule
`0 * * * *` pointing at:

    python -m scripts.send_appointment_reminders

Idempotent: every reminder is marked as sent right after it is delivered
(Appointment.reminder_48h_sent_at / reminder_24h_sent_at), so running it twice
or retrying after a crash never double-sends. A failed delivery is not marked,
so the next run retries it.

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
            for appointment, kind in appointments.reminders_due(db, tenant):
                contact = appointment.contact
                try:
                    appointments.send_reminder(
                        db, tenant, appointment, contact, kind=kind, use_notes_summary=True
                    )
                except appointments.ReminderDeliveryError:
                    db.rollback()
                    logger.warning(
                        "Reminder NOT delivered (will retry next run): tenant=%s kind=%s appointment=%s",
                        tenant.slug, kind, appointment.id,
                    )
                    continue
                # Commit one by one so a later failure can't undo reminders already delivered.
                db.commit()
                sent_count += 1
                logger.info(
                    "Reminder sent: tenant=%s kind=%s contact=%s appointment=%s",
                    tenant.slug, kind, contact.phone, appointment.id,
                )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    logger.info("Done. %d reminder(s) sent.", sent_count)
    return sent_count


if __name__ == "__main__":
    run()