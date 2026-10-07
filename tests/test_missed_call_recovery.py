from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import select
from twilio.request_validator import RequestValidator

from app.config import settings
from app.main import app
from app.models import Audit, DB, Message, Number, RecoveryJob, Suppression, Tenant
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


def enable_recovery(client, number_id, *, advanced=False):
    payload = {
        "enabled": advanced,
        "greeting": "Thank you for calling Example Limited.",
        "options": [],
        "never_miss": ["callback"],
        "emergency_mode": "closed" if advanced else "normal",
        "missed_call_sms_enabled": True,
        "missed_call_sms_message": "Sorry we missed your call. Reply and our team will get back to you.",
    }
    response = client.put(
        f"/api/numbers/{number_id}/call-routing",
        json=payload,
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text


def start_simple_call(client, tenant_id, monkeypatch, call_sid="CArecover1", caller="+447700900001"):
    monkeypatch.setattr("app.main.within_budget", lambda client, tenant: True)
    monkeypatch.setattr("app.main.tenant_client", lambda tenant: MagicMock())
    response = signed_post(
        client,
        "/webhooks/twilio/voice",
        tenant_id,
        {"To": "+442080001001", "From": caller, "CallSid": call_sid},
    )
    assert response.status_code == 200
    assert "<Dial" in response.text


def finish_missed_call(client, tenant_id, call_sid="CArecover1"):
    return signed_post(
        client,
        "/webhooks/twilio/voice-status",
        tenant_id,
        {
            "CallSid": call_sid,
            "CallStatus": "completed",
            "DialCallStatus": "no-answer",
            "DialCallDuration": "0",
        },
    )


def recovery_messages(db, tenant_id):
    return db.scalars(
        select(Message).where(
            Message.tenant_id == tenant_id,
            Message.direction == "outbound",
            Message.request_key.like("recovery:missed-call:%"),
        )
    ).all()


def test_opted_in_simple_forwarding_queues_exactly_one_recovery_sms(monkeypatch):
    client, tenant_id, number_id = account()
    enable_recovery(client, number_id)
    start_simple_call(client, tenant_id, monkeypatch)

    assert finish_missed_call(client, tenant_id).status_code == 200
    assert finish_missed_call(client, tenant_id).status_code == 200

    with DB() as db:
        messages = recovery_messages(db, tenant_id)
        recoveries = db.scalars(
            select(RecoveryJob).where(
                RecoveryJob.tenant_id == tenant_id,
                RecoveryJob.kind == "missed_call",
            )
        ).all()
        assert len(messages) == 1
        assert messages[0].peer == "+447700900001"
        assert messages[0].status == "queued"
        assert "missed your call" in messages[0].body.lower()
        assert len(recoveries) == 1


def test_advanced_closed_route_uses_same_missed_call_recovery(monkeypatch):
    client, tenant_id, number_id = account()
    enable_recovery(client, number_id, advanced=True)
    monkeypatch.setattr("app.main.within_budget", lambda client, tenant: True)
    monkeypatch.setattr("app.main.tenant_client", lambda tenant: MagicMock())

    response = signed_post(
        client,
        "/webhooks/twilio/voice",
        tenant_id,
        {"To": "+442080001001", "From": "+447700900002", "CallSid": "CAclosed1"},
    )
    assert response.status_code == 200
    assert "currently closed" in response.text.lower()

    with DB() as db:
        messages = recovery_messages(db, tenant_id)
        assert len(messages) == 1
        assert messages[0].peer == "+447700900002"


def test_opted_out_caller_gets_recovery_task_but_no_sms(monkeypatch):
    client, tenant_id, number_id = account()
    enable_recovery(client, number_id)
    with DB.begin() as db:
        db.add(Suppression(tenant_id=tenant_id, peer="+447700900001"))
    start_simple_call(client, tenant_id, monkeypatch)

    assert finish_missed_call(client, tenant_id).status_code == 200

    with DB() as db:
        assert recovery_messages(db, tenant_id) == []
        assert db.scalar(
            select(RecoveryJob).where(
                RecoveryJob.tenant_id == tenant_id,
                RecoveryJob.kind == "missed_call",
            )
        ) is not None


def test_business_number_plan_never_queues_recovery_sms(monkeypatch):
    client, tenant_id, number_id = account()
    enable_recovery(client, number_id)
    with DB.begin() as db:
        db.get(Tenant, tenant_id).plan = "business"
    start_simple_call(client, tenant_id, monkeypatch)

    assert finish_missed_call(client, tenant_id).status_code == 200

    with DB() as db:
        assert recovery_messages(db, tenant_id) == []


def test_sms_allowance_exhaustion_skips_text_without_losing_recovery_task(monkeypatch):
    client, tenant_id, number_id = account()
    enable_recovery(client, number_id)
    with DB.begin() as db:
        for index in range(100):
            db.add(
                Message(
                    tenant_id=tenant_id,
                    number_id=number_id,
                    peer="+447700900099",
                    direction="outbound",
                    body="x",
                    segment_units=1,
                    channel="sms",
                    request_key=f"existing-{index}",
                    status="sent",
                )
            )
    start_simple_call(client, tenant_id, monkeypatch)

    assert finish_missed_call(client, tenant_id).status_code == 200

    with DB() as db:
        assert recovery_messages(db, tenant_id) == []
        assert db.scalar(
            select(RecoveryJob).where(
                RecoveryJob.tenant_id == tenant_id,
                RecoveryJob.kind == "missed_call",
            )
        ) is not None
        audit = db.scalar(
            select(Audit).where(
                Audit.tenant_id == tenant_id,
                Audit.action == "missed_call.sms_skipped",
            )
        )
        assert audit is not None
        assert audit.detail == "allowance"
