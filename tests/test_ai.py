from unittest.mock import MagicMock
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
import pytest
from sqlalchemy import select
from app import ai
from app.config import settings
from app.models import AIProfile, Audit, Call, DB, Message, Suppression, VoiceSession
from test_flows import HEADERS, customer, enable, number, twilio_post


@pytest.fixture
def setup_ai(monkeypatch):
    monkeypatch.setattr(settings, "ai_url", "http://localhost:11434/v1")
    monkeypatch.setattr(settings, "ai_model", "test-model")
    c, t = customer()
    enable(t)
    n = number(t)
    profile = {
        "enabled": True,
        "voice_enabled": True,
        "business_info": "Acme provides bookkeeping. Open Monday to Friday 9 to 5.",
        "greeting": "Welcome to Acme. How can I help?",
    }
    assert c.put("/api/ai/profile", json=profile, headers=HEADERS).status_code == 200
    return c, t, n


def test_profile_configuration_and_isolation(monkeypatch):
    c, t = customer()
    monkeypatch.setattr(settings, "ai_url", "")
    assert c.put("/api/ai/profile", json={"enabled": True, "business_info": "Company"}, headers=HEADERS).status_code == 409
    assert c.put("/api/ai/profile", json={"business_info": "Company"}, headers=HEADERS).status_code == 200
    other, _ = customer("other@example.com")
    assert other.get("/api/ai/profile").json()["business_info"] == ""
    assert c.put("/api/ai/profile", json={}).status_code == 403


def test_draft_idempotency_no_send_and_tenant_history(setup_ai, monkeypatch):
    c, t, n = setup_ai
    with DB.begin() as db:
        m = Message(tenant_id=t, number_id=n, peer="+447700900123", body="When are you open?", direction="inbound", status="received")
        db.add(m)
        db.flush()
        mid = m.id
        db.add(
            Message(
                tenant_id=t, number_id=n, peer="+447700900999", body="Private unrelated message", direction="inbound", status="received"
            )
        )
    seen = []
    monkeypatch.setattr(ai, "generate", lambda p, h: seen.append(h) or "We are open Monday to Friday, 9 to 5.")
    a = c.post("/api/ai/drafts/" + mid, headers=HEADERS)
    assert a.status_code == 200
    assert c.post("/api/ai/drafts/" + mid, headers=HEADERS).json()["id"] == a.json()["id"]
    assert ai.draft_one()
    assert c.get("/api/ai/drafts/" + a.json()["id"]).json()["status"] == "ready"
    assert seen == [[{"role": "user", "content": "When are you open?"}]]
    with DB() as db:
        assert not db.scalars(select(Message).where(Message.direction == "outbound")).all()
        assert len(db.scalars(select(Audit).where(Audit.action == "ai.request")).all()) == 1
    other, _ = customer("other@example.com")
    assert other.get("/api/ai/drafts/" + a.json()["id"]).status_code == 404
    assert other.post("/api/ai/drafts/" + mid, headers=HEADERS).status_code == 404


def test_draft_optout_and_quota(setup_ai, monkeypatch):
    c, t, n = setup_ai
    with DB.begin() as db:
        m = Message(tenant_id=t, number_id=n, peer="+447700900123", body="Hello", direction="inbound", status="received")
        db.add(m)
        db.flush()
        mid = m.id
        db.add(Suppression(tenant_id=t, peer=m.peer))
    assert c.post("/api/ai/drafts/" + mid, headers=HEADERS).status_code == 409
    with DB.begin() as db:
        db.delete(db.scalar(select(Suppression)))
    monkeypatch.setattr(settings, "ai_daily_requests", 0)
    assert c.post("/api/ai/drafts/" + mid, headers=HEADERS).status_code == 429


def voice_start(setup_ai, monkeypatch):
    c, t, n = setup_ai
    monkeypatch.setattr("app.main.tenant_client", lambda t: MagicMock())
    monkeypatch.setattr("app.main.within_budget", lambda c, t: True)
    r = twilio_post(c, "/webhooks/twilio/voice", t, {"CallSid": "CAvoice", "To": "+442080001001", "From": "+447700900123"})
    assert r.status_code == 200
    gather = ET.fromstring(r.text).find("Gather")
    assert gather is not None
    return c, t, gather.attrib["action"]


def test_voice_signed_turn_replay_and_transfer(setup_ai, monkeypatch):
    c, t, n = setup_ai
    with DB.begin() as db:
        from app.models import Number

        db.get(Number, n).forwarding = "+447700900111"
    c, t, action = voice_start(setup_ai, monkeypatch)
    path = urlparse(action).path + "?" + urlparse(action).query
    calls = []
    monkeypatch.setattr(ai, "generate", lambda p, h: calls.append(h) or "We provide bookkeeping.")
    assert c.post(path, data={"CallSid": "CAvoice", "SpeechResult": "Hello"}).status_code == 403
    params = {"CallSid": "CAvoice", "SpeechResult": "What do you do?"}
    first = twilio_post(c, path, t, params)
    assert first.status_code == 200 and "bookkeeping" in first.text
    assert twilio_post(c, path, t, params).text == first.text
    assert len(calls) == 1
    next_action = ET.fromstring(first.text).find("Gather").attrib["action"]
    next_path = urlparse(next_action).path + "?" + urlparse(next_action).query
    human = twilio_post(c, next_path, t, {"CallSid": "CAvoice", "Digits": "0"})
    assert "<Dial" in human.text and "+447700900111" in human.text
    assert len(calls) == 1
    with DB() as db:
        session = db.get(VoiceSession, "CAvoice")
        assert "What do you do" not in session.history
        assert session.status == "handoff"


def test_voice_failure_fallback_and_duration(setup_ai, monkeypatch):
    c, t, action = voice_start(setup_ai, monkeypatch)
    path = urlparse(action).path + "?" + urlparse(action).query

    def fail(*args):
        raise RuntimeError("provider payload secret")

    monkeypatch.setattr(ai, "generate", fail)
    result = twilio_post(c, path, t, {"CallSid": "CAvoice", "SpeechResult": "Tell me more"})
    assert result.status_code == 200 and "<Hangup" in result.text
    assert "secret" not in result.text
    assert (
        twilio_post(
            c, "/webhooks/twilio/voice-status", t, {"CallSid": "CAvoice", "CallStatus": "completed", "CallDuration": "125"}
        ).status_code
        == 200
    )
    with DB() as db:
        assert db.get(Call, "CAvoice").billed_minutes == 3


def test_provider_response_contract(monkeypatch):
    monkeypatch.setattr(settings, "ai_url", "http://localhost:11434/v1")
    monkeypatch.setattr(settings, "ai_model", "test")
    import httpx

    original = httpx.Client

    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json={"choices": [{"message": {"content": "A short helpful answer."}}]})

    monkeypatch.setattr(ai.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    assert ai.generate(AIProfile(business_info="Acme"), [{"role": "user", "content": "hello"}]) == "A short helpful answer."


def test_voice_missing_token_and_daily_limit(setup_ai, monkeypatch):
    c, t, action = voice_start(setup_ai, monkeypatch)
    assert twilio_post(c, "/webhooks/twilio/ai-voice", t, {"CallSid": "CAvoice", "SpeechResult": "Hello"}).status_code == 403
    monkeypatch.setattr(settings, "ai_daily_requests", 0)
    path = urlparse(action).path + "?" + urlparse(action).query
    r = twilio_post(c, path, t, {"CallSid": "CAvoice", "SpeechResult": "Hello"})
    assert r.status_code == 200 and "<Hangup" in r.text


def test_provider_invalid_reply_is_not_delivered(setup_ai, monkeypatch):
    import httpx

    original = httpx.Client
    monkeypatch.setattr(
        ai.httpx,
        "Client",
        lambda **kwargs: original(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "x" * 501}}]})), **kwargs
        ),
    )
    with pytest.raises(RuntimeError):
        ai.generate(AIProfile(business_info="Acme"), [{"role": "user", "content": "hi"}])
