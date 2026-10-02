import json
from unittest.mock import MagicMock
from sqlalchemy import select
from app import ai
from app.models import AIProfile, DB, Message, Number
from app.worker import send_one
from test_ai import setup_ai as setup_ai
from test_flows import twilio_post


def test_autonomous_whatsapp_sender_and_window(setup_ai, monkeypatch):
    c, t, n = setup_ai
    with DB.begin() as db:
        number = db.get(Number, n)
        number.whatsapp, number.whatsapp_sender = "approved", "whatsapp:+442080001001"
        db.get(AIProfile, t).autonomous = True
    monkeypatch.setattr(ai, "generate", lambda *a: json.dumps({"reply": "Hello.", "intent": "answer", "args": {}}))
    params = {"MessageSid": "SMwhatsapp", "To": "whatsapp:+442080001001", "From": "whatsapp:+447700900123", "Body": "Hello"}
    assert twilio_post(c, "/webhooks/twilio/inbound", t, params).status_code == 200
    assert ai.draft_one()
    provider = MagicMock()
    provider.messages.create.return_value = MagicMock(sid="SMsent", status="sent")
    monkeypatch.setattr("app.worker.tenant_client", lambda *a: provider)
    monkeypatch.setattr("app.worker.within_budget", lambda *a: True)
    assert send_one()
    assert provider.messages.create.call_args.kwargs["from_"] == "whatsapp:+442080001001"
    assert provider.messages.create.call_args.kwargs["to"] == "whatsapp:+447700900123"
    with DB() as db:
        assert all(m.channel == "whatsapp" for m in db.scalars(select(Message)))
    assert c.get("/api/messages", params={"number_id": n, "peer": "+447700900123", "channel": "sms"}).json() == []


def test_rcs_uses_bound_service_only(setup_ai, monkeypatch):
    c, t, n = setup_ai
    with DB.begin() as db:
        number = db.get(Number, n)
        number.rcs, number.rcs_sender, number.rcs_service_sid = "approved", "rcs:approved_agent", "MG" + "a" * 32
        db.get(AIProfile, t).autonomous = True
    monkeypatch.setattr(ai, "generate", lambda *a: json.dumps({"reply": "Hello.", "intent": "answer", "args": {}}))
    params = {
        "MessageSid": "SMrcs",
        "To": "rcs:approved_agent",
        "From": "+447700900123",
        "Body": "Hello",
        "ChannelPrefix": "rcs",
        "MessagingServiceSid": "MG" + "a" * 32,
    }
    assert twilio_post(c, "/webhooks/twilio/inbound", t, params).status_code == 200
    assert ai.draft_one()
    provider = MagicMock()
    provider.messages.create.return_value = MagicMock(sid="SMsent", status="sent")
    monkeypatch.setattr("app.worker.tenant_client", lambda *a: provider)
    monkeypatch.setattr("app.worker.within_budget", lambda *a: True)
    assert send_one()
    assert provider.messages.create.call_args.kwargs["messaging_service_sid"] == "MG" + "a" * 32
    params.update(MessageSid="SMwrong", MessagingServiceSid="MG" + "b" * 32)
    assert twilio_post(c, "/webhooks/twilio/inbound", t, params).status_code == 404
