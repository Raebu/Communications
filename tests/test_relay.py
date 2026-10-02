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


def test_voice_action_preview_confirmation_and_replay(setup_ai, monkeypatch):
    import json
    from sqlalchemy import select
    from app import ai
    from app.models import AIProfile, ActionJob, Lead

    c, t, token, sid = start(setup_ai, monkeypatch)
    with DB.begin() as db:
        db.get(AIProfile, t).autonomous = True
    monkeypatch.setattr(
        ai,
        "generate",
        lambda *args: json.dumps(
            {"reply": "I can pass this on.", "intent": "lead", "args": {"summary": "Caller requests bookkeeping help"}}
        ),
    )
    path = "/ws/voice?tenant=" + t
    signature = RequestValidator("testtoken").compute_signature(settings.public_url + path, {})
    with c.websocket_connect(path, headers={"x-twilio-signature": signature}) as ws:
        ws.send_json({"type": "setup", "accountSid": sid, "callSid": "CArelay", "customParameters": {"sessionToken": token}})
        ws.send_json({"type": "prompt", "voicePrompt": "Please pass on my enquiry", "last": True})
        assert "confirm this action" in ws.receive_json()["token"]
        assert ws.receive_json()["last"]
        with DB() as db:
            assert not db.scalar(select(Lead))
        ws.send_json({"type": "prompt", "voicePrompt": "Confirm this action", "last": True})
        assert "recorded" in ws.receive_json()["token"]
        assert ws.receive_json()["last"]
        ws.send_json({"type": "prompt", "voicePrompt": "Confirm this action", "last": True})
        assert "no current action" in ws.receive_json()["token"]
        assert ws.receive_json()["last"]
        ws.send_json({"type": "dtmf", "digit": "0"})
        assert ws.receive_json()["type"] == "end"
    with DB() as db:
        assert len(db.scalars(select(Lead)).all()) == 1
        assert db.scalar(select(ActionJob)).status == "completed"


def test_interruption_revokes_voice_proposal(setup_ai, monkeypatch):
    import json
    from sqlalchemy import select
    from app import ai
    from app.models import AIProfile, ActionJob, Lead

    c, t, token, sid = start(setup_ai, monkeypatch)
    with DB.begin() as db:
        db.get(AIProfile, t).autonomous = True
    monkeypatch.setattr(
        ai, "generate", lambda *args: json.dumps({"reply": "I can pass this on.", "intent": "lead", "args": {"summary": "Caller enquiry"}})
    )
    path = "/ws/voice?tenant=" + t
    signature = RequestValidator("testtoken").compute_signature(settings.public_url + path, {})
    with c.websocket_connect(path, headers={"x-twilio-signature": signature}) as ws:
        ws.send_json({"type": "setup", "accountSid": sid, "callSid": "CArelay", "customParameters": {"sessionToken": token}})
        ws.send_json({"type": "prompt", "voicePrompt": "Pass this on", "last": True})
        assert "confirm this action" in ws.receive_json()["token"]
        assert ws.receive_json()["last"]
        ws.send_json({"type": "interrupt", "utteranceUntilInterrupt": "Pass this enquiry"})
        ws.send_json({"type": "prompt", "voicePrompt": "Confirm this action", "last": True})
        assert "no current action" in ws.receive_json()["token"]
        assert ws.receive_json()["last"]
        ws.send_json({"type": "dtmf", "digit": "0"})
        ws.receive_json()
    with DB() as db:
        assert not db.scalar(select(Lead))
        assert db.scalar(select(ActionJob)).status == "cancelled"


def test_verified_voice_can_cancel_owned_sms_booking(setup_ai, monkeypatch):
    import json
    from datetime import timedelta
    from app import ai
    from app.models import AIProfile, Booking, Department, now
    from app.security import decrypt
    from test_autonomy import inbound
    from test_identity import code

    c, t, token, sid = start(setup_ai, monkeypatch)
    with DB.begin() as db:
        db.get(AIProfile, t).autonomous = True
        d = Department(tenant_id=t, name="Appointments", bookings_enabled=True)
        db.add(d)
        db.flush()
        b = Booking(
            tenant_id=t,
            department_id=d.id,
            peer="+447700900123",
            starts_at=now() + timedelta(days=2),
            ends_at=now() + timedelta(days=2, minutes=30),
            request_key="owned-before-link",
        )
        db.add(b)
        db.flush()
        bid = b.id
    inbound(c, t, "LINK", "SMvoiceproof")
    with DB() as db:
        proof = code(db)
    monkeypatch.setattr(ai, "generate", lambda *a: json.dumps({"reply": "I can cancel.", "intent": "cancel", "args": {"booking_id": bid}}))
    path = "/ws/voice?tenant=" + t
    signature = RequestValidator("testtoken").compute_signature(settings.public_url + path, {})
    words = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    with c.websocket_connect(path, headers={"x-twilio-signature": signature}) as ws:
        ws.send_json({"type": "setup", "accountSid": sid, "callSid": "CArelay", "customParameters": {"sessionToken": token}})
        ws.send_json({"type": "prompt", "voicePrompt": "Verify code " + " ".join(words[int(d)] for d in proof), "last": True})
        assert "channels are linked" in ws.receive_json()["token"]
        assert ws.receive_json()["last"]
        ws.send_json({"type": "prompt", "voicePrompt": "Cancel my appointment", "last": True})
        assert "Cancel appointment" in ws.receive_json()["token"]
        assert ws.receive_json()["last"]
        ws.send_json({"type": "prompt", "voicePrompt": "Confirm this action", "last": True})
        assert "cancelled" in ws.receive_json()["token"]
        assert ws.receive_json()["last"]
        ws.send_json({"type": "dtmf", "digit": "0"})
        ws.receive_json()
    with DB() as db:
        assert db.get(Booking, bid).status == "cancelled"
        messages = decrypt(db.get(VoiceSession, "CArelay").history)["messages"]
        assert messages[0]["content"] == "[Verification response redacted]"
