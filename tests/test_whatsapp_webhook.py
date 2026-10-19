"""
Tests for the WhatsApp Cloud API (Meta) webhook. Helpers are tested directly;
the HTTP route runs with a real HMAC signature and a fake sender (no network).

Run with:  .venv/bin/python -m pytest tests/test_whatsapp_webhook.py
"""

import hashlib
import hmac
import json
import types
import uuid

import pytest

from app.api.routes import whatsapp as wa
from app.core.db import SessionLocal
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message
from app.services import meta_whatsapp as mw

SECRET = "secreto-de-prueba"


def _sign(raw: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _payload(wa_id, text, *, msg_type="text", msg_id=None):
    message = {"from": wa_id, "id": msg_id or f"wamid.{uuid.uuid4().hex}", "type": msg_type}
    if msg_type == "text":
        message["text"] = {"body": text}
    return {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "1221053261101149"},
        "contacts": [{"wa_id": wa_id, "profile": {"name": "Prueba"}}],
        "messages": [message],
    }}]}]}


def test_signature():
    raw = b'{"a":1}'
    assert mw.is_valid_signature(raw, SECRET, _sign(raw))
    assert not mw.is_valid_signature(raw + b" ", SECRET, _sign(raw))
    assert not mw.is_valid_signature(raw, "otro", _sign(raw))
    assert not mw.is_valid_signature(raw, SECRET, None)
    assert not mw.is_valid_signature(raw, "", _sign(raw))
    assert not mw.is_valid_signature(raw, SECRET, "sha1=abc")


def test_verify_challenge():
    ok = {"hub.mode": "subscribe", "hub.verify_token": "tok", "hub.challenge": "123"}
    assert mw.verify_challenge(ok, "tok") == "123"
    assert mw.verify_challenge({**ok, "hub.verify_token": "mal"}, "tok") is None
    assert mw.verify_challenge(ok, "") is None


def test_parse_inbound_text_media_and_statuses():
    msgs = mw.parse_inbound(_payload("18095550311", "Hola"))
    assert len(msgs) == 1 and msgs[0].text == "Hola" and msgs[0].phone == "+18095550311"
    assert msgs[0].profile_name == "Prueba" and msgs[0].phone_number_id == "1221053261101149"
    assert mw.parse_inbound(_payload("18095550311", None, msg_type="audio"))[0].text is None
    statuses = {"entry": [{"changes": [{"value": {"statuses": [{"id": "x", "status": "read"}]}}]}]}
    assert mw.parse_inbound(statuses) == []
    assert mw.parse_inbound({}) == []


def test_send_text_builds_the_graph_call(monkeypatch):
    seen = {}

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["body"] = json.loads(request.data)
        return Resp()

    monkeypatch.setattr(mw.urllib.request, "urlopen", fake_urlopen)
    assert mw.send_text("TOKEN", "999", "+18095550311", "Hola")
    assert seen["url"].endswith("/999/messages")
    assert seen["auth"] == "Bearer TOKEN"
    assert seen["body"]["to"] == "18095550311" and seen["body"]["text"]["body"] == "Hola"


def test_send_text_failure_does_not_raise(monkeypatch):
    def boom(*a, **k): raise OSError("sin red")
    monkeypatch.setattr(mw.urllib.request, "urlopen", boom)
    assert mw.send_text("T", "1", "1809", "x") is False


@pytest.fixture
def client(monkeypatch):
    testclient = pytest.importorskip("fastapi.testclient")
    from fastapi import FastAPI

    settings = types.SimpleNamespace(
        meta_app_secret=SECRET, meta_verify_token="verif", meta_access_token="TOKEN",
        bot_enabled=True, whatsapp_default_tenant_slug="grip", gemini_api_key="",
    )
    sent = []
    monkeypatch.setattr(wa, "get_settings", lambda: settings)
    monkeypatch.setattr(wa, "_gemini_client", None)
    monkeypatch.setattr(wa.mw, "send_text", lambda token, pid, to, body: sent.append((to, body)) or True)
    app = FastAPI()
    app.include_router(wa.router)
    c = testclient.TestClient(app)
    c.settings, c.sent = settings, sent
    return c


@pytest.fixture
def wa_id():
    n = f"1555{uuid.uuid4().int % 10_000_000:07d}"
    yield n
    db = SessionLocal()
    contact = db.query(Contact).filter(Contact.phone == "+" + n).one_or_none()
    if contact:
        for conv in db.query(Conversation).filter(Conversation.contact_id == contact.id).all():
            db.query(Message).filter(Message.conversation_id == conv.id).delete(synchronize_session=False)
            db.delete(conv)
        db.delete(contact)
        db.commit()
    db.close()


def _post(client, payload, *, sign=True):
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["X-Hub-Signature-256"] = _sign(raw)
    return client.post("/webhooks/whatsapp", content=raw, headers=headers)


def test_handshake(client):
    r = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "verif", "hub.challenge": "42"})
    assert r.status_code == 200 and r.text == "42"
    assert client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "42"}).status_code == 403


def test_bad_signature_and_missing_secret(client, wa_id):
    assert _post(client, _payload(wa_id, "Hola"), sign=False).status_code == 403
    client.settings.meta_app_secret = ""
    assert _post(client, _payload(wa_id, "Hola")).status_code == 503


def test_greeting_is_answered_and_stored(client, wa_id):
    assert _post(client, _payload(wa_id, "Hola")).status_code == 200
    assert client.sent and client.sent[0][0] == wa_id and "bienvenida" in client.sent[0][1]
    db = SessionLocal()
    try:
        contact = db.query(Contact).filter(Contact.phone == "+" + wa_id).one()
        assert contact.whatsapp_profile_name == "Prueba"
    finally:
        db.close()


def test_new_patient_flow_and_audio_reply(client, wa_id):
    _post(client, _payload(wa_id, "quiero una cita"))
    assert "SeNaSa" in client.sent[-1][1]
    _post(client, _payload(wa_id, "1"))
    _post(client, _payload(wa_id, "Ana Gómez"))
    _post(client, _payload(wa_id, "no habla bien"))
    assert "solicitud" in client.sent[-1][1]
    _post(client, _payload(wa_id, None, msg_type="audio"))
    assert client.sent[-1][1] == mw.UNSUPPORTED_TYPE_REPLY


def test_kill_switch_sends_nothing(client, wa_id):
    client.settings.bot_enabled = False
    assert _post(client, _payload(wa_id, "Hola")).status_code == 200
    assert client.sent == []