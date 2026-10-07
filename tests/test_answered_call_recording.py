import json
from datetime import timedelta
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import select
from twilio.request_validator import RequestValidator

import app.call_recording as call_recording
import app.routing_autopilot as routing_autopilot
from app.call_routing import RoutingUpdate
from app.config import settings
from app.main import app
from app.models import (
    Audit,
    Call,
    CallRouting,
    CustomerEvent,
    DB,
    IntelligenceJob,
    IntelligenceProfile,
    Number,
    Tenant,
    now,
)
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


def routing_payload(**overrides):
    payload = {
        "enabled": False,
        "greeting": "Thank you for calling Example Limited.",
        "fallback": "",
        "ring_seconds": 20,
        "options": [],
        "record_answered_calls": False,
        "transcribe_answered_calls": False,
        "recording_retention_days": 30,
        "recording_announcement": "This call may be recorded for service and quality purposes.",
    }
    payload.update(overrides)
    return payload


def start_simple_call(client, tenant_id, monkeypatch, call_sid="CArecord1"):
    monkeypatch.setattr("app.main.within_budget", lambda client, tenant: True)
    monkeypatch.setattr("app.main.tenant_client", lambda tenant: MagicMock())
    return signed_post(
        client,
        "/webhooks/twilio/voice",
        tenant_id,
        {
            "To": "+442080001001",
            "From": "+447700900001",
            "CallSid": call_sid,
        },
    )


def test_answered_call_recording_and_transcription_are_off_by_default():
    config = RoutingUpdate()
    assert config.record_answered_calls is False
    assert config.transcribe_answered_calls is False
    assert config.recording_retention_days == 30


def test_transcription_requires_recording():
    try:
        RoutingUpdate(transcribe_answered_calls=True)
        assert False, "Transcription must not be enabled without recording"
    except ValueError:
        pass


def test_simple_forwarding_adds_disclosure_dual_recording_and_live_transcription(monkeypatch):
    client, tenant_id, number_id = account()
    response = client.put(
        f"/api/numbers/{number_id}/call-routing",
        json=routing_payload(
            record_answered_calls=True,
            transcribe_answered_calls=True,
            recording_retention_days=14,
            recording_announcement="This call is recorded for service quality.",
        ),
        headers=HEADERS,
    )
    assert response.status_code == 200

    call = start_simple_call(client, tenant_id, monkeypatch)
    assert call.status_code == 200
    assert "This call is recorded for service quality." in call.text
    assert 'record="record-from-answer-dual"' in call.text
    assert "/webhooks/twilio/call-recording" in call.text
    assert "<Start><Transcription" in call.text
    assert "/webhooks/twilio/call-transcription" in call.text
    assert 'languageCode="en-GB"' in call.text
    assert "+447700900099" in call.text


def test_fallback_human_leg_does_not_repeat_disclosure_or_restart_transcription(monkeypatch):
    client, tenant_id, number_id = account()
    payload = routing_payload(
        enabled=True,
        fallback="+447700900099",
        options=[
            {
                "digit": "1",
                "label": "Sales",
                "description": "new enquiry",
                "action": "dial",
                "destination": "+447700900011",
                "destinations": ["+447700900011"],
                "strategy": "simultaneous",
            }
        ],
        never_miss=["fallback", "callback"],
        record_answered_calls=True,
        transcribe_answered_calls=True,
        recording_announcement="This call is recorded for service quality.",
    )
    assert client.put(
        f"/api/numbers/{number_id}/call-routing",
        json=payload,
        headers=HEADERS,
    ).status_code == 200

    monkeypatch.setattr("app.main.within_budget", lambda client, tenant: True)
    monkeypatch.setattr("app.main.tenant_client", lambda tenant: MagicMock())
    menu = signed_post(
        client,
        "/webhooks/twilio/voice",
        tenant_id,
        {
            "To": "+442080001001",
            "From": "+447700900001",
            "CallSid": "CAfallback1",
        },
    )
    assert "<Gather" in menu.text

    first_leg = signed_post(
        client,
        "/webhooks/twilio/voice-menu",
        tenant_id,
        {"CallSid": "CAfallback1", "Digits": "1"},
    )
    assert "This call is recorded for service quality." in first_leg.text
    assert first_leg.text.count("<Transcription") == 1
    assert 'record="record-from-answer-dual"' in first_leg.text

    fallback = signed_post(
        client,
        "/webhooks/twilio/voice-route-result",
        tenant_id,
        {
            "CallSid": "CAfallback1",
            "DialCallStatus": "no-answer",
            "DialCallDuration": "0",
        },
    )
    assert "+447700900099" in fallback.text
    assert "This call is recorded for service quality." not in fallback.text
    assert "<Transcription" not in fallback.text
    assert 'record="record-from-answer-dual"' in fallback.text


def test_ai_handoff_redirects_into_shared_human_media_policy(monkeypatch):
    client, tenant_id, number_id = account()
    assert client.put(
        f"/api/numbers/{number_id}/call-routing",
        json=routing_payload(
            record_answered_calls=True,
            recording_announcement="This call is recorded before transfer.",
        ),
        headers=HEADERS,
    ).status_code == 200

    with DB.begin() as db:
        call = Call(
            sid="CAaihandoff",
            tenant_id=tenant_id,
            number_id=number_id,
            destination="+447700900099",
            reserved_minutes=2,
        )
        db.add(call)
        db.flush()
        db.add(
            CustomerEvent(
                tenant_id=tenant_id,
                channel="voice",
                kind="call.inbound",
                source_id=call.sid,
                encrypted_payload=encrypt(
                    {"from": "+447700900001", "to": "+442080001001"}
                ),
            )
        )

    response = signed_post(
        client,
        "/webhooks/twilio/voice-human-fallback",
        tenant_id,
        {"CallSid": "CAaihandoff"},
    )
    assert response.status_code == 200
    assert "This call is recorded before transfer." in response.text
    assert 'record="record-from-answer-dual"' in response.text
    assert "+447700900099" in response.text


def test_recording_callback_is_idempotent_and_never_persists_media_url():
    client, tenant_id, number_id = account()
    assert client.put(
        f"/api/numbers/{number_id}/call-routing",
        json=routing_payload(
            record_answered_calls=True,
            recording_retention_days=14,
        ),
        headers=HEADERS,
    ).status_code == 200

    with DB.begin() as db:
        db.add(
            Call(
                sid="CArecordcb",
                tenant_id=tenant_id,
                number_id=number_id,
                destination="+447700900099",
                reserved_minutes=2,
            )
        )
        db.add(
            CustomerEvent(
                tenant_id=tenant_id,
                channel="voice",
                kind="call.inbound",
                source_id="CArecordcb",
                encrypted_payload=encrypt(
                    {"from": "+447700900001", "to": "+442080001001"}
                ),
            )
        )

    params = {
        "CallSid": "CArecordcb",
        "RecordingSid": "RErecordcb",
        "RecordingStatus": "completed",
        "RecordingDuration": "35",
        "RecordingChannels": "2",
        "RecordingSource": "DialVerb",
        "RecordingUrl": "https://api.example.invalid/private-recording",
    }
    assert signed_post(
        client, "/webhooks/twilio/call-recording", tenant_id, params
    ).status_code == 204
    assert signed_post(
        client, "/webhooks/twilio/call-recording", tenant_id, params
    ).status_code == 204

    with DB() as db:
        events = db.scalars(
            select(CustomerEvent).where(
                CustomerEvent.tenant_id == tenant_id,
                CustomerEvent.kind == "call.recording",
            )
        ).all()
        assert len(events) == 1
        payload = decrypt(events[0].encrypted_payload)
        assert payload["recording_sid"] == "RErecordcb"
        assert payload["retention_days"] == 14
        assert payload["deleted"] is False
        assert "RecordingUrl" not in payload
        assert "private-recording" not in events[0].encrypted_payload


def test_final_transcription_builds_labelled_post_call_transcript_and_queues_intelligence():
    client, tenant_id, number_id = account()
    with DB.begin() as db:
        db.add(
            IntelligenceProfile(
                tenant_id=tenant_id,
                enabled=True,
                analyse_calls=True,
                analyse_voicemail=False,
                analyse_messages=False,
            )
        )
        db.add(
            Call(
                sid="CAtranscript",
                tenant_id=tenant_id,
                number_id=number_id,
                destination="+447700900099",
                reserved_minutes=2,
            )
        )
        db.add(
            CustomerEvent(
                tenant_id=tenant_id,
                channel="voice",
                kind="call.inbound",
                source_id="CAtranscript",
                encrypted_payload=encrypt(
                    {"from": "+447700900001", "to": "+442080001001"}
                ),
            )
        )

    first = {
        "CallSid": "CAtranscript",
        "TranscriptionSid": "GTtranscript",
        "TranscriptionEvent": "transcription-content",
        "Final": "true",
        "SequenceId": "1",
        "Track": "inbound_track",
        "TranscriptionData": json.dumps(
            {"transcript": "I need a renewal quote.", "confidence": 0.95}
        ),
    }
    second = {
        "CallSid": "CAtranscript",
        "TranscriptionSid": "GTtranscript",
        "TranscriptionEvent": "transcription-content",
        "Final": "true",
        "SequenceId": "2",
        "Track": "outbound_track",
        "TranscriptionData": json.dumps(
            {"transcript": "I can help with that.", "confidence": 0.94}
        ),
    }
    assert signed_post(
        client, "/webhooks/twilio/call-transcription", tenant_id, first
    ).status_code == 204
    assert signed_post(
        client, "/webhooks/twilio/call-transcription", tenant_id, first
    ).status_code == 204
    assert signed_post(
        client, "/webhooks/twilio/call-transcription", tenant_id, second
    ).status_code == 204
    assert signed_post(
        client,
        "/webhooks/twilio/call-transcription",
        tenant_id,
        {
            "CallSid": "CAtranscript",
            "TranscriptionSid": "GTtranscript",
            "TranscriptionEvent": "transcription-stopped",
        },
    ).status_code == 204

    with DB() as db:
        utterances = db.scalars(
            select(CustomerEvent).where(
                CustomerEvent.tenant_id == tenant_id,
                CustomerEvent.kind == "call.transcript.utterance",
            )
        ).all()
        assert len(utterances) == 2
        transcript = db.scalar(
            select(CustomerEvent).where(
                CustomerEvent.tenant_id == tenant_id,
                CustomerEvent.kind == "call.transcribed",
            )
        )
        assert transcript is not None
        payload = decrypt(transcript.encrypted_payload)
        assert "Customer: I need a renewal quote." in payload["transcript"]
        assert "Agent: I can help with that." in payload["transcript"]
        job = db.scalar(
            select(IntelligenceJob).where(IntelligenceJob.event_id == transcript.id)
        )
        assert job is not None


def test_call_transcript_does_not_queue_intelligence_without_separate_opt_in():
    client, tenant_id, number_id = account()
    with DB.begin() as db:
        db.add(
            IntelligenceProfile(
                tenant_id=tenant_id,
                enabled=True,
                analyse_calls=False,
                analyse_voicemail=True,
                analyse_messages=False,
            )
        )
        event = CustomerEvent(
            tenant_id=tenant_id,
            channel="voice",
            kind="call.transcribed",
            encrypted_payload=encrypt({"transcript": "Customer: Hello"}),
        )
        db.add(event)
        db.flush()
        event_id = event.id

    from app.conversation_intelligence import queue_event

    with DB.begin() as db:
        event = db.get(CustomerEvent, event_id)
        assert queue_event(db, event) is None


def test_recording_retention_deletes_provider_media_and_marks_encrypted_metadata(monkeypatch):
    client, tenant_id, number_id = account()
    old = now() - timedelta(days=15)
    with DB.begin() as db:
        event = CustomerEvent(
            tenant_id=tenant_id,
            channel="voice",
            kind="call.recording",
            source_id="REold",
            occurred_at=old,
            encrypted_payload=encrypt(
                {
                    "call_sid": "CAold",
                    "recording_sid": "REold",
                    "status": "completed",
                    "retention_days": 14,
                    "deleted": False,
                }
            ),
        )
        db.add(event)
        db.flush()
        event_id = event.id

    provider = MagicMock()
    monkeypatch.setattr(call_recording, "tenant_client", lambda tenant: provider)
    assert call_recording.recording_retention_one() is True
    provider.recordings.assert_called_once_with("REold")
    provider.recordings.return_value.delete.assert_called_once()

    with DB() as db:
        event = db.get(CustomerEvent, event_id)
        payload = decrypt(event.encrypted_payload)
        assert payload["deleted"] is True
        assert payload["deleted_at"]
        audit = db.scalar(
            select(Audit).where(
                Audit.tenant_id == tenant_id,
                Audit.action == "call.recording.deleted",
            )
        )
        assert audit is not None


def test_recording_retention_keeps_media_until_due(monkeypatch):
    client, tenant_id, number_id = account()
    with DB.begin() as db:
        db.add(
            CustomerEvent(
                tenant_id=tenant_id,
                channel="voice",
                kind="call.recording",
                source_id="REfresh",
                occurred_at=now() - timedelta(days=2),
                encrypted_payload=encrypt(
                    {
                        "call_sid": "CAfresh",
                        "recording_sid": "REfresh",
                        "status": "completed",
                        "retention_days": 14,
                        "deleted": False,
                    }
                ),
            )
        )

    provider = MagicMock()
    monkeypatch.setattr(call_recording, "tenant_client", lambda tenant: provider)
    assert call_recording.recording_retention_one() is False
    provider.recordings.assert_not_called()


def test_autopilot_cannot_enable_recording_or_transcription_without_explicit_instruction(monkeypatch):
    client, tenant_id, number_id = account()
    generated = routing_payload(
        record_answered_calls=True,
        transcribe_answered_calls=True,
    )
    monkeypatch.setattr(
        routing_autopilot,
        "_json_completion",
        lambda *args, **kwargs: generated,
    )
    monkeypatch.setattr(routing_autopilot, "quota", lambda *args, **kwargs: None)

    response = client.post(
        f"/api/routing-autopilot/numbers/{number_id}/draft",
        json={"instruction": "Make the routing more professional but keep the same privacy settings."},
        headers=HEADERS,
    )
    assert response.status_code == 422


def test_autopilot_allows_explicit_recording_request(monkeypatch):
    client, tenant_id, number_id = account()
    generated = routing_payload(
        record_answered_calls=True,
        transcribe_answered_calls=False,
    )
    monkeypatch.setattr(
        routing_autopilot,
        "_json_completion",
        lambda *args, **kwargs: generated,
    )
    monkeypatch.setattr(routing_autopilot, "quota", lambda *args, **kwargs: None)

    response = client.post(
        f"/api/routing-autopilot/numbers/{number_id}/draft",
        json={"instruction": "Please enable answered-call recording and keep everything else unchanged."},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["draft"]["record_answered_calls"] is True
