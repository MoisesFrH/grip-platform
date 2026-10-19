"""
Pure helpers for Meta's WhatsApp Cloud API (no database, no FastAPI).

Standard library only (no `requests`, no SDK): the webhook payload is parsed
from JSON, the X-Hub-Signature-256 header is verified with HMAC-SHA256 using
the app secret, and replies are sent with one HTTPS call to the Graph API.
"""

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

GRAPH_VERSION = "v25.0"
UNSUPPORTED_TYPE_REPLY = (
    "Por ahora solo puedo leer mensajes de texto. ¿Me cuentas por escrito en qué te puedo ayudar?"
)


@dataclass
class InboundMessage:
    wa_id: str           # sender, digits only, e.g. "18095550311"
    message_id: str
    text: str | None     # None for non-text messages (audio, image, ...)
    profile_name: str | None
    phone_number_id: str  # the business number that received it

    @property
    def phone(self) -> str:
        return "+" + self.wa_id.lstrip("+")


def is_valid_signature(raw_body: bytes, app_secret: str, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def verify_challenge(params: dict[str, str], verify_token: str) -> str | None:
    """Meta's GET handshake: returns the challenge to echo, or None to refuse."""
    if not verify_token:
        return None
    if params.get("hub.mode") == "subscribe" and hmac.compare_digest(
        params.get("hub.verify_token", ""), verify_token
    ):
        return params.get("hub.challenge", "")
    return None


def parse_inbound(payload: dict) -> list[InboundMessage]:
    """Every user message in a webhook payload. Delivery/read receipts
    ("statuses") and anything malformed are ignored."""
    found: list[InboundMessage] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            phone_number_id = (value.get("metadata") or {}).get("phone_number_id", "")
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts", []) or []}
            for message in value.get("messages", []) or []:
                sender = message.get("from")
                message_id = message.get("id")
                if not sender or not message_id:
                    continue
                text = None
                if message.get("type") == "text":
                    text = (message.get("text") or {}).get("body")
                found.append(InboundMessage(
                    wa_id=sender, message_id=message_id, text=text,
                    profile_name=names.get(sender), phone_number_id=phone_number_id,
                ))
    return found


def send_text(access_token: str, phone_number_id: str, to_wa_id: str, body: str, *, timeout: float = 10.0) -> bool:
    """Sends a free-form text (valid inside the 24h window after the user's
    last message). Never raises: a failed send is logged and reported as False."""
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{phone_number_id}/messages"
    data = json.dumps({
        "messaging_product": "whatsapp",
        "to": to_wa_id.lstrip("+"),
        "type": "text",
        "text": {"body": body},
    }).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        logger.error("WhatsApp send failed: HTTP %s %s", exc.code, exc.read()[:300])
    except Exception:
        logger.exception("WhatsApp send failed")
    return False