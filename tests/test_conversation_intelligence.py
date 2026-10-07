from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

import app.conversation_intelligence as ci
from app.config import settings
from app.main import app
from app.models import (
    Customer,
    CustomerEvent,
    CustomerState,
    DB,
    IntelligenceJob,
    IntelligenceProfile,
    Outcome,
    Promise,
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
    return client, client.get("/api/me").json()["tenant"]["id"]


def test_disabled_intelligence_never_queues_customer_text():
    client, tenant_id = account()
    with DB.begin() as db:
        event = CustomerEvent(
            tenant_id=tenant_id,
            channel="sms",
            kind="message.inbound",
            source_id="SM1",
            encrypted_payload=encrypt({"body": "Please send the quote tomorrow"}),
        )
        db.add(event)
        db.flush()
        assert ci.queue_event(db, event) is None

    with DB() as db:
        assert db.scalar(select(IntelligenceJob)) is None


def test_only_selected_sources_are_queued():
    client, tenant_id = account()
    with DB.begin() as db:
        db.add(IntelligenceProfile(
            tenant_id=tenant_id,
            enabled=True,
            analyse_voicemail=True,
            analyse_messages=False,
        ))
        message = CustomerEvent(
            tenant_id=tenant_id,
            channel="sms",
            kind="message.inbound",
            source_id="SM1",
            encrypted_payload=encrypt({"body": "Hello"}),
        )
        voicemail = CustomerEvent(
            tenant_id=tenant_id,
            channel="voice",
            kind="voicemail.transcribed",
            source_id="TR1",
            encrypted_payload=encrypt({"transcript": "Please call me back"}),
        )
        db.add_all([message, voicemail])
        db.flush()
        assert ci.queue_event(db, message) is None
        assert ci.queue_event(db, voicemail) is not None

    with DB() as db:
        jobs = db.scalars(select(IntelligenceJob)).all()
        assert len(jobs) == 1
        assert jobs[0].event_id == voicemail.id


def test_structured_result_validation_rejects_extra_fields_and_bad_ranges():
    valid = {
        "summary": "Customer asked for a renewal quote.",
        "sentiment": "neutral",
        "risk_delta": 2,
        "revenue_signal_pence": 500000,
        "promises": [],
        "actions": ["Prepare renewal quote"],
        "confidence": 0.9,
    }
    assert ci._validate_result(valid)["revenue_signal_pence"] == 500000

    with_extra = {**valid, "secret": "ignore"}
    try:
        ci._validate_result(with_extra)
        assert False, "Unexpected fields should fail"
    except RuntimeError:
        pass

    bad_risk = {**valid, "risk_delta": 99}
    try:
        ci._validate_result(bad_risk)
        assert False, "Out-of-range risk should fail"
    except RuntimeError:
        pass


def test_low_confidence_result_does_not_change_customer_brain():
    client, tenant_id = account()
    with DB.begin() as db:
        customer = Customer(tenant_id=tenant_id)
        db.add(customer)
        db.flush()
        db.add(CustomerState(
            customer_id=customer.id,
            tenant_id=tenant_id,
            risk_score=10,
            revenue_signal=100,
            encrypted_state=encrypt({"summary": "Existing summary"}),
        ))
        event = CustomerEvent(
            tenant_id=tenant_id,
            customer_id=customer.id,
            channel="voice",
            kind="voicemail.transcribed",
            encrypted_payload=encrypt({"transcript": "Potential quote"}),
        )
        db.add(event)
        db.flush()
        ci._apply_result(db, event, {
            "summary": "New summary",
            "sentiment": "negative",
            "risk_delta": 20,
            "revenue_signal_pence": 999999,
            "promises": [],
            "actions": ["Call customer"],
            "confidence": 0.3,
        })
        customer_id = customer.id

    with DB() as db:
        state = db.get(CustomerState, customer_id)
        assert state.risk_score == 10
        assert state.revenue_signal == 100
        assert decrypt(state.encrypted_state)["summary"] == "Existing summary"
        assert db.scalar(select(Outcome)) is None


def test_high_confidence_analysis_creates_encrypted_actions_and_explicit_future_promise():
    client, tenant_id = account()
    with DB.begin() as db:
        customer = Customer(tenant_id=tenant_id)
        db.add(customer)
        db.flush()
        event = CustomerEvent(
            tenant_id=tenant_id,
            customer_id=customer.id,
            channel="voice",
            kind="voicemail.transcribed",
            encrypted_payload=encrypt({"transcript": "I will send the signed order tomorrow. Please prepare the renewal quote."}),
        )
        db.add(event)
        db.flush()
        due = (now() + timedelta(days=1)).isoformat()
        ci._apply_result(db, event, {
            "summary": "Customer intends to send the signed order and wants a renewal quote.",
            "sentiment": "positive",
            "risk_delta": -3,
            "revenue_signal_pence": 480000,
            "promises": [{"commitment": "Send the signed order", "due_at": due}],
            "actions": ["Prepare renewal quote"],
            "confidence": 0.92,
        })
        customer_id = customer.id

    with DB() as db:
        state = db.get(CustomerState, customer_id)
        assert state.revenue_signal == 480000
        assert state.risk_score == 0
        assert "renewal quote" in decrypt(state.encrypted_state)["summary"]
        outcome = db.scalar(select(Outcome))
        promise = db.scalar(select(Promise))
        assert outcome is not None
        assert promise is not None
        assert "Prepare renewal quote" not in outcome.encrypted_payload
        assert decrypt(outcome.encrypted_payload)["note"] == "Prepare renewal quote"
        assert "signed order" not in promise.encrypted_commitment
        assert decrypt(promise.encrypted_commitment)["commitment"] == "Send the signed order"


def test_worker_applies_mocked_analysis_only_for_opted_in_event(monkeypatch):
    client, tenant_id = account()
    with DB.begin() as db:
        db.add(IntelligenceProfile(
            tenant_id=tenant_id,
            enabled=True,
            analyse_voicemail=True,
            analyse_messages=False,
        ))
        customer = Customer(tenant_id=tenant_id)
        db.add(customer)
        db.flush()
        event = CustomerEvent(
            tenant_id=tenant_id,
            customer_id=customer.id,
            channel="voice",
            kind="voicemail.transcribed",
            source_id="TR-worker",
            encrypted_payload=encrypt({"transcript": "Please call me about the £1,000 renewal"}),
        )
        db.add(event)
        db.flush()
        job = ci.queue_event(db, event)
        job_id = job.id

    monkeypatch.setattr(ci, "_request_analysis", lambda text: {
        "summary": "Customer wants to discuss renewal.",
        "sentiment": "neutral",
        "risk_delta": 1,
        "revenue_signal_pence": 100000,
        "promises": [],
        "actions": ["Call customer about renewal"],
        "confidence": 0.88,
    })
    assert ci.intelligence_one() is True

    with DB() as db:
        job = db.get(IntelligenceJob, job_id)
        assert job.status == "completed"
        assert "Customer wants" not in job.result
        result = decrypt(job.result)
        assert result["revenue_signal_pence"] == 100000


def test_owner_must_explicitly_enable_intelligence(monkeypatch):
    client, tenant_id = account()
    old_url, old_model = settings.ai_url, settings.ai_model
    settings.ai_url, settings.ai_model = "https://ai.example.invalid", "model"
    try:
        response = client.put(
            "/api/conversation-intelligence/settings",
            json={
                "enabled": True,
                "analyse_voicemail": True,
                "analyse_messages": False,
                "retention_days": 60,
            },
            headers=HEADERS,
        )
        assert response.status_code == 200
        with DB() as db:
            profile = db.get(IntelligenceProfile, tenant_id)
            assert profile.enabled is True
            assert profile.analyse_voicemail is True
            assert profile.analyse_messages is False
    finally:
        settings.ai_url, settings.ai_model = old_url, old_model
