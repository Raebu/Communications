from unittest.mock import MagicMock
import pytest
from starlette.websockets import WebSocketDisconnect
from twilio.request_validator import RequestValidator
from app.config import settings
from app.models import DB, Tenant, VoiceSession
from app import relay
from test_ai import setup_ai as setup_ai
from test_flows import twilio_post


def start(setup_ai, monkeypatch):
    c, t, n = setup_ai
    monkeypatch.setattr(settings, "voice_streaming_enabled", True)
    monkeypatch.setattr("app.main.within_budget", lambda *a: True)
    monkeypatch.setattr("app.main.tenant_client", lambda *a: MagicMock())
    result = twilio_post(c, "/webhooks/twilio/voice", t, {"CallSid": "CArelay", "To": "+442080001001", "From": "+447700900123"})
    assert "ConversationRelay" in result.text
    with DB() as db:
        session = db.get(VoiceSession, "CArelay")
        tenant = db.get(Tenant, t)
        return c, t, session.token, tenant.twilio_sid


def test_unsigned_websocket_rejected(setup_ai, monkeypatch):
    c, t, token, sid = start(setup_ai, monkeypatch)
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws/voice?tenant=" + t):
            pass


def test_signed_stream_and_human_exit(setup_ai, monkeypatch):
    c, t, token, sid = start(setup_ai, monkeypatch)

    async def chunks(*args):
        yield "Hello "
        yield "there."

    monkeypatch.setattr(relay, "stream_answer", chunks)
    path = "/ws/voice?tenant=" + t
    signature = RequestValidator("testtoken").compute_signature(settings.public_url + path, {})
    with c.websocket_connect(path, headers={"x-twilio-signature": signature}) as ws:
        ws.send_json({"type": "setup", "accountSid": sid, "callSid": "CArelay", "customParameters": {"sessionToken": token}})
        ws.send_json({"type": "prompt", "voicePrompt": "Hi", "last": True})
        assert ws.receive_json()["token"] == "Hello "
        assert ws.receive_json()["token"] == "there."
        assert ws.receive_json()["last"] is True
        ws.send_json({"type": "dtmf", "digit": "0"})
        assert ws.receive_json()["type"] == "end"
    with DB() as db:
        session = db.get(VoiceSession, "CArelay")
        assert session.status == "handoff" and "Hello" not in session.history
