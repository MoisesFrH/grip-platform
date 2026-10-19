"""
WhatsApp Cloud API (Meta) webhook: the real-channel twin of
POST /simulator/message.

GET  /webhooks/whatsapp  -> Meta's one-time verification handshake.
POST /webhooks/whatsapp  -> a patient's message. It goes through the SAME
orchestrator the simulator uses (so it lands in the CRM exactly like a
simulator chat: conversation, messages, secretary cases, new-patient intake
card) and the bot's reply is sent back through the Graph API.

Not included yet: a secretary answering the patient on WhatsApp from the CRM.

Safety rails: fails closed without META_APP_SECRET / a valid signature;
BOT_ENABLED=false makes the bot ignore every message (kill switch).
"""

import json
import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.db import get_db
from app.models.tenant import Tenant
from app.services import meta_whatsapp as mw
from app.services.gemini_client import GeminiClient
from app.services.orchestrator import process_inbound_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["whatsapp"])

_gemini_client: GeminiClient | None
try:
    _gemini_client = GeminiClient() if get_settings().gemini_api_key else None
except Exception:
    _gemini_client = None


@router.get("/whatsapp")
def verify_webhook(request: Request) -> Response:
    challenge = mw.verify_challenge(dict(request.query_params), get_settings().meta_verify_token)
    if challenge is None:
        return Response(content="Verificación rechazada.", status_code=403, media_type="text/plain")
    return Response(content=challenge, media_type="text/plain")


def _handle(db: Session, message: mw.InboundMessage) -> str | None:
    settings = get_settings()
    tenant = db.query(Tenant).filter(Tenant.slug == settings.whatsapp_default_tenant_slug).one_or_none()
    if tenant is None:
        logger.warning("WhatsApp webhook: tenant %r not found", settings.whatsapp_default_tenant_slug)
        return None
    if message.text is None:
        return mw.UNSUPPORTED_TYPE_REPLY
    result = process_inbound_message(
        db, tenant, message.phone, message.text,
        twilio_message_id=message.message_id,
        whatsapp_profile_name=message.profile_name,
        gemini_client=_gemini_client,
    )
    db.commit()
    return result.reply_text


@router.post("/whatsapp")
async def receive_webhook(request: Request, db: Session = Depends(get_db)) -> Response:
    settings = get_settings()
    raw = await request.body()
    if not settings.meta_app_secret:
        return Response(content="WhatsApp no configurado.", status_code=503, media_type="text/plain")
    if not mw.is_valid_signature(raw, settings.meta_app_secret, request.headers.get("X-Hub-Signature-256")):
        return Response(content="Firma inválida.", status_code=403, media_type="text/plain")

    if not settings.bot_enabled:
        return Response(content="ok", media_type="text/plain")

    try:
        messages = mw.parse_inbound(json.loads(raw))
    except ValueError:
        return Response(content="ok", media_type="text/plain")

    for message in messages:
        try:
            reply = await run_in_threadpool(_handle, db, message)
        except Exception:
            logger.exception("WhatsApp webhook failed for message %s", message.message_id)
            db.rollback()
            reply = "Disculpa, tuve un problema técnico. Un momento y vuelve a escribirme, por favor."
        if reply and settings.meta_access_token:
            await run_in_threadpool(
                mw.send_text, settings.meta_access_token, message.phone_number_id, message.wa_id, reply
            )
    # Always 200 so Meta does not retry the same message in a loop.
    return Response(content="ok", media_type="text/plain")