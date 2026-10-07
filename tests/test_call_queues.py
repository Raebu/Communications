from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import select
from twilio.request_validator import RequestValidator

import app.call_queue as call_queue
from app.config import settings
from app.main import app
from app.models import Call, CustomerEvent, DB, Number, QueueTicket, Tenant
from app.security import decrypt, encrypt


HEADERS = {"origin": "http://localhost:8000", "x-requested-with": "Raeburn"}


def account():
    client = TestClient(app)
    assert client.post(
        "/api/register",
        json={
            "email": "owner@example.com",
            "password": "correct-horse-battery",
            "company": "Example Ltd",
            "accept_terms": True,
        },
        headers=HEADERS,
    ).status_code == 200
    assert client.post(
        "/api/login",
        json={"email": "owner@example.com", "password": "correct-horse-battery"},
        headers=HEADERS,
    ).status_code == 200
    tenant_id = client.get("/api/me").json()["tenant"]["id"]
    with DB.begin() as db:
        tenant = db.get(Tenant, tenant_id)
        tenant.status = "approved"
        tenant.billing_status = "active"
        tenant.plan = "connect"
        tenant.twilio_sid = "AC" + tenant_id.replace("-", "")[:32]
        tenant.credentials = encrypt(
            {"key_sid": "SKtest", "key_secret": "secret", "auth_token": "testtoken"}
        )
        number = Number(
            tenant_id=tenant_id,
            phone="+442080001001",
            sid="PN" + tenant_id.replace("-", "")[:32],
            sms=True,
            voice=True,
            forwarding="+447700900099",
        )
        db.add(number)
        db.flush()
        number_id = number.id
    return client, tenant_id, number_id


def signed_post(client, path, tenant_id, params):
    values = dict(params)
    with DB() as db:
        tenant = db.get(Tenant, tenant_id)
        values["AccountSid"] = tenant.twilio_sid
        token = decrypt(tenant.credentials)["auth_token"]
    signature = RequestValidator(token).compute_signature(settings.public_url + path, values)
    return client.post(path, data=values, headers={"x-twilio-signature": signature})


def queue_config():
    return {
        "enabled": True,
        "greeting": "Thank you for calling Example Limited.",
        "fallback": "+447700900099",
        "ring_seconds": 20,
        "options": [
            {
                "digit": "1",
                "label": "Sales queue",
                "description": "new enquiry, pricing, quote",
                "action": "queue",
                "destination": "+447700900011",
                "destinations": ["+447700900011", "+447700900012"],
                "strategy": "priority",
                "queue_callback_enabled": True,
            }
        ],
        "never_miss": ["fallback", "callback"],
        "record_answered_calls": True,
        "transcribe_answered_calls": True,
        "recording_announcement": "This call may be recorded for service and quality purposes.",
    }


def start_queued_call(client, tenant_id, number_id, monkeypatch, call_sid="CAqueue1"):
    assert client.put(
        f"/api/numbers/{number_id}/call-routing",
        json=queue_config(),
        headers=HEADERS,
    ).status_code == 200
    monkeypatch.setattr("app.main.within_budget", lambda client, tenant: True)
    monkeypatch.setattr("app.main.tenant_client", lambda tenant: MagicMock())
    initial = signed_post(
        client,
        "/webhooks/twilio/voice",
        tenant_id,
        {"To": "+442080001001", "From": "+447700900001", "CallSid": call_sid},
    )
    assert initial.status_code == 200
    assert "<Gather" in initial.text
    queued = signed_post(
        client,
        "/webhooks/twilio/voice-menu",
        tenant_id,
        {"CallSid": call_sid, "Digits": "1"},
    )
    return queued


def test_queue_route_creates_real_enqueue_and_durable_ticket(monkeypatch):
    client, tenant_id, number_id = account()
    queued = start_queued_call(client, tenant_id, number_id, monkeypatch)

    assert queued.status_code == 200
    assert "<Enqueue" in queued.text
    assert "/webhooks/twilio/queue-wait?ticket=" in queued.text
    assert "/webhooks/twilio/queue-result?ticket=" in queued.text
    assert "This call may be recorded for service and quality purposes." in queued.text

    with DB() as db:
        ticket = db.scalar(select(QueueTicket))
        assert ticket is not None
        assert ticket.status == "waiting"
        assert ticket.queue_name.startswith("rq-")
        payload = decrypt(ticket.encrypted_payload)
        assert payload["members"] == ["+447700900011", "+447700900012"]
        assert payload["callback_enabled"] is True
        entered_at = ticket.entered_at
        call = db.get(Call, "CAqueue1")
        assert call.status == "queued"
        assert entered_at is not None


def test_queue_wait_announces_position_and_offers_place_preserving_callback(monkeypatch):
    client, tenant_id, number_id = account()
    start_queued_call(client, tenant_id, number_id, monkeypatch)
    with DB() as db:
        ticket = db.scalar(select(QueueTicket))
        ticket_id = ticket.id

    wait = signed_post(
        client,
        f"/webhooks/twilio/queue-wait?ticket={ticket_id}",
        tenant_id,
        {
            "CallSid": "CAqueue1",
            "QueuePosition": "3",
            "AvgQueueTime": "125",
            "CurrentQueueSize": "5",
        },
    )
    assert wait.status_code == 200
    assert "number 3 in the queue" in wait.text
    assert "about 3 minutes" in wait.text
    assert "Press 1" in wait.text
    assert "queue-wait-choice" in wait.text


def test_virtual_callback_keeps_original_queue_priority(monkeypatch):
    client, tenant_id, number_id = account()
    start_queued_call(client, tenant_id, number_id, monkeypatch)
    with DB() as db:
        ticket = db.scalar(select(QueueTicket))
        ticket_id = ticket.id
        entered_at = ticket.entered_at

    choice = signed_post(
        client,
        f"/webhooks/twilio/queue-wait-choice?ticket={ticket_id}",
        tenant_id,
        {"CallSid": "CAqueue1", "Digits": "1"},
    )
    assert choice.status_code == 200
    assert "<Leave" in choice.text

    with DB() as db:
        ticket = db.get(QueueTicket, ticket_id)
        assert ticket.status == "virtual_waiting"
        assert ticket.entered_at == entered_at
        assert ticket.next_attempt_at is not None

    left = signed_post(
        client,
        f"/webhooks/twilio/queue-result?ticket={ticket_id}",
        tenant_id,
        {"CallSid": "CAqueue1", "QueueResult": "leave", "QueueTime": "40"},
    )
    assert "place has been saved" in left.text


def test_worker_hunts_one_agent_and_persists_provider_call(monkeypatch):
    client, tenant_id, number_id = account()
    start_queued_call(client, tenant_id, number_id, monkeypatch)
    provider = MagicMock()
    usage = MagicMock(price_unit="usd", price="0.10")
    provider.usage.records.this_month.list.return_value = [usage]
    provider.calls.create.return_value = MagicMock(sid="CAagent1")
    monkeypatch.setattr(call_queue, "tenant_client", lambda tenant: provider)

    assert call_queue.queue_hunt_one() is True
    provider.calls.create.assert_called_once()
    kwargs = provider.calls.create.call_args.kwargs
    assert kwargs["to"] == "+447700900011"
    assert kwargs["from_"] == "+442080001001"
    assert "queue-agent?ticket=" in kwargs["url"]

    with DB() as db:
        ticket = db.scalar(select(QueueTicket))
        assert ticket.status == "agent_calling"
        assert ticket.provider_sid == "CAagent1"
        assert ticket.current_destination == "+447700900011"


def test_ambiguous_agent_call_creation_goes_to_review_and_is_not_retried(monkeypatch):
    client, tenant_id, number_id = account()
    start_queued_call(client, tenant_id, number_id, monkeypatch)
    provider = MagicMock()
    usage = MagicMock(price_unit="usd", price="0.10")
    provider.usage.records.this_month.list.return_value = [usage]
    provider.calls.create.side_effect = TimeoutError()
    monkeypatch.setattr(call_queue, "tenant_client", lambda tenant: provider)

    assert call_queue.queue_hunt_one() is True
    assert call_queue.queue_hunt_one() is False
    provider.calls.create.assert_called_once()
    with DB() as db:
        assert db.scalar(select(QueueTicket)).status == "review"


def test_answered_agent_dequeues_native_caller_with_recording_and_transcription(monkeypatch):
    client, tenant_id, number_id = account()
    start_queued_call(client, tenant_id, number_id, monkeypatch)
    with DB.begin() as db:
        ticket = db.scalar(select(QueueTicket).with_for_update())
        ticket.provider_sid = "CAagent2"
        ticket.status = "agent_calling"
        ticket_id = ticket.id

    agent = signed_post(
        client,
        f"/webhooks/twilio/queue-agent?ticket={ticket_id}",
        tenant_id,
        {"CallSid": "CAagent2"},
    )
    assert agent.status_code == 200
    assert "<Queue" in agent.text
    assert 'record="record-from-answer-dual"' in agent.text
    assert "<Transcription" in agent.text
    assert "queue-connect-notice" in agent.text


def test_virtual_callback_agent_dials_customer_from_business_number(monkeypatch):
    client, tenant_id, number_id = account()
    start_queued_call(client, tenant_id, number_id, monkeypatch)
    with DB.begin() as db:
        ticket = db.scalar(select(QueueTicket).with_for_update())
        payload = decrypt(ticket.encrypted_payload)
        payload["resume_status"] = "virtual_waiting"
        ticket.encrypted_payload = encrypt(payload)
        ticket.provider_sid = "CAagent3"
        ticket.status = "agent_calling"
        ticket_id = ticket.id

    agent = signed_post(
        client,
        f"/webhooks/twilio/queue-agent?ticket={ticket_id}",
        tenant_id,
        {"CallSid": "CAagent3"},
    )
    assert agent.status_code == 200
    assert "+447700900001" in agent.text
    assert 'callerId="+442080001001"' in agent.text
    assert "queue-agent-result" in agent.text


def test_failed_virtual_callback_cools_down_without_losing_priority(monkeypatch):
    client, tenant_id, number_id = account()
    start_queued_call(client, tenant_id, number_id, monkeypatch)
    with DB.begin() as db:
        ticket = db.scalar(select(QueueTicket).with_for_update())
        entered_at = ticket.entered_at
        payload = decrypt(ticket.encrypted_payload)
        payload["resume_status"] = "virtual_waiting"
        ticket.encrypted_payload = encrypt(payload)
        ticket.provider_sid = "CAagent4"
        ticket.status = "calling_customer"
        ticket_id = ticket.id

    result = signed_post(
        client,
        f"/webhooks/twilio/queue-agent-result?ticket={ticket_id}",
        tenant_id,
        {"CallSid": "CAagent4", "DialCallStatus": "no-answer"},
    )
    assert result.status_code == 200
    with DB() as db:
        ticket = db.get(QueueTicket, ticket_id)
        assert ticket.status == "virtual_waiting"
        assert ticket.entered_at == entered_at
        assert ticket.next_attempt_at is not None
        assert ticket.provider_sid == ""


def test_queue_agent_media_callback_maps_to_original_customer_call(monkeypatch):
    client, tenant_id, number_id = account()
    start_queued_call(client, tenant_id, number_id, monkeypatch)
    with DB.begin() as db:
        ticket = db.scalar(select(QueueTicket).with_for_update())
        ticket.provider_sid = "CAagentmedia"
        ticket.status = "bridging"

    recording = signed_post(
        client,
        "/webhooks/twilio/call-recording",
        tenant_id,
        {
            "CallSid": "CAagentmedia",
            "RecordingSid": "REqueue",
            "RecordingStatus": "completed",
            "RecordingDuration": "20",
            "RecordingChannels": "2",
            "RecordingSource": "DialVerb",
        },
    )
    assert recording.status_code == 204

    with DB() as db:
        event = db.scalar(
            select(CustomerEvent).where(
                CustomerEvent.tenant_id == tenant_id,
                CustomerEvent.kind == "call.recording",
            )
        )
        assert event is not None
        assert decrypt(event.encrypted_payload)["call_sid"] == "CAqueue1"


def test_queue_simulation_shows_queue_and_virtual_callback():
    client, tenant_id, number_id = account()
    response = client.post(
        "/api/routing-autopilot/simulate",
        json={
            "config": queue_config(),
            "digit": "1",
            "selected_destination_answers": False,
        },
        headers=HEADERS,
    )
    assert response.status_code == 200
    labels = [step["label"].lower() for step in response.json()["steps"]]
    assert any("real queue" in label for label in labels)
    assert any("keep their place" in label for label in labels)
    assert response.json()["ends_safely"] is True
