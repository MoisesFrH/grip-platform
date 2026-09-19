"""
Dev-only route to exercise the Gemini integration by hand against a seeded
tenant, without going through Twilio/webhooks yet. NOT for production: no
auth, no conversation persistence, no safety layer in front of it — those
land with the webhook + router work. Requires GEMINI_API_KEY to be set.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.tenant import Tenant
from app.services.gemini_client import GeminiClient, UnsupportedIntentError

router = APIRouter(prefix="/dev", tags=["dev"])


class DevChatRequest(BaseModel):
    tenant_slug: str
    intent: str
    message: str


class DevChatResponse(BaseModel):
    text: str
    tools_used: list[str]
    tool_results: dict
    usage: dict


@router.post("/chat", response_model=DevChatResponse)
def dev_chat(payload: DevChatRequest, db: Session = Depends(get_db)) -> DevChatResponse:
    tenant = db.query(Tenant).filter(Tenant.slug == payload.tenant_slug).one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"Unknown tenant slug: {payload.tenant_slug}")

    client = GeminiClient()
    try:
        result = client.answer(tenant=tenant, intent=payload.intent, user_message=payload.message)
    except UnsupportedIntentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return DevChatResponse(
        text=result.text,
        tools_used=result.tools_used,
        tool_results=result.tool_results,
        usage=result.usage,
    )
