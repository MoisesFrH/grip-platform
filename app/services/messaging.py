"""
The one place a proactive (non-reply) WhatsApp message goes out from —
today, that's only the appointment reminder job. Everything else in this
codebase is reactive (a patient writes, the bot/agent replies), so this is
new: it's the first outbound message that isn't a reply to something.

There is no Twilio integration in this codebase yet (per the project's own
sequencing: build and validate the whole pipeline against the /simulator
first, wire up Twilio once that's proven). So `send_whatsapp_message` is a
deliberate stub — when Twilio is wired up (architecture doc §9), this is
the one function that needs a real implementation (a Twilio REST API call
sending a WhatsApp template message, since a day-before reminder is
typically sent outside any existing 24h conversation window and Meta
requires an approved template for that). Every other caller in this
module is already written against this function, not against Twilio
directly, so that swap doesn't touch orchestrator.py, appointments.py, or
the reminder script.
"""

import logging

logger = logging.getLogger(__name__)


def send_whatsapp_message(phone: str, body: str) -> None:
    """Stub: logs the message that *would* be sent. Once Twilio is
    integrated, replace this body with a real
    client.messages.create(from_=tenant.whatsapp_number, to=f"whatsapp:{phone}", body=body)
    call (or, more likely, a template-message call — see the docstring
    above). The DB-side bookkeeping (recording the outbound Message row,
    marking the appointment's reminder as sent) happens in the caller
    either way, so it's correct regardless of whether this stub or a real
    Twilio call is behind it.
    """
    logger.info("WHATSAPP SEND STUB -> %s: %s", phone, body)