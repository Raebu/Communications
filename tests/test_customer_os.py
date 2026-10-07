from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.customer_os import obligation_one, record_event
from app.main import app
from app.models import Customer, CustomerState, DB, Promise, RecoveryJob, now
from app.security import decrypt, encrypt


HEADERS = {"origin": "http://localhost:8000", "x-requested-with": "Raeburn"}


def account(email="owner@example.com"):
    client = TestClient(app)
    assert client.post(
        "/api/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "company": "Example Ltd",
            "accept_terms": True,
        },
        headers=HEADERS,
    ).status_code == 200
    assert client.post(
        "/api/login",
        json={"email": email, "password": "correct-horse-battery"},
        headers=HEADERS,
    ).status_code == 200
    tenant_id = client.get("/api/me").json()["tenant"]["id"]
    return client, tenant_id


def test_guarantee_creates_recovery_deadline():
    client, tenant_id = account()
    response = client.post(
        "/api/customer-os/guarantees",
        json={
            "name": "Respond to missed calls",
            "event_kind": "call.missed",
            "max_minutes": 10,
            "action": "callback_task",
            "enabled": True,
        },
        headers=HEADERS,
    )
    assert response.status_code == 200

    with DB.begin() as db:
        event = record_event(
            db,
            tenant_id,
            "call.missed",
            "voice",
            "CAexample",
            None,
            {"from": "+447700900001"},
        )
        assert event.kind == "call.missed"

    with DB() as db:
        job = db.scalar(select(RecoveryJob).where(RecoveryJob.tenant_id == tenant_id))
        assert job is not None
        assert job.kind == "guarantee"
        assert job.status == "queued"
        detail = decrypt(job.encrypted_payload)
        assert detail["rule_name"] == "Respond to missed calls"


def test_overdue_promise_escalates_and_increases_relationship_risk():
    client, tenant_id = account()
    with DB.begin() as db:
        customer = Customer(tenant_id=tenant_id)
        db.add(customer)
        db.flush()
        db.add(CustomerState(customer_id=customer.id, tenant_id=tenant_id, risk_score=65))
        promise = Promise(
            tenant_id=tenant_id,
            customer_id=customer.id,
            owner="Martin",
            due_at=now() - timedelta(minutes=1),
            encrypted_commitment=encrypt({"commitment": "Send the revised quote"}),
        )
        db.add(promise)
        db.flush()
        promise_id = promise.id
        customer_id = customer.id

    assert obligation_one() is True

    with DB() as db:
        promise = db.get(Promise, promise_id)
        state = db.get(CustomerState, customer_id)
        recovery = db.scalar(
            select(RecoveryJob).where(
                RecoveryJob.tenant_id == tenant_id,
                RecoveryJob.kind == "promise_overdue",
            )
        )
        assert promise.status == "overdue"
        assert state.risk_score == 75
        assert recovery is not None
        assert recovery.status == "attention"


def test_executive_brief_counts_interactions_promises_recovery_and_revenue():
    client, tenant_id = account()
    with DB.begin() as db:
        customer = Customer(tenant_id=tenant_id)
        db.add(customer)
        db.flush()
        db.add(
            CustomerState(
                customer_id=customer.id,
                tenant_id=tenant_id,
                risk_score=80,
                revenue_signal=320000,
            )
        )
        db.add(
            Promise(
                tenant_id=tenant_id,
                customer_id=customer.id,
                owner="Sales",
                due_at=now() + timedelta(hours=1),
                encrypted_commitment=encrypt({"commitment": "Call customer"}),
            )
        )
        db.add(
            RecoveryJob(
                tenant_id=tenant_id,
                customer_id=customer.id,
                kind="missed_call",
                status="attention",
                due_at=now(),
                encrypted_payload=encrypt({"call_sid": "CA1"}),
            )
        )
        record_event(db, tenant_id, "message.inbound", "sms", "SM1", customer.id, {"preview": "Hello"})

    brief = client.get("/api/customer-os/executive-brief")
    assert brief.status_code == 200
    data = brief.json()
    assert data["interactions_24h"] >= 1
    assert data["open_promises"] == 1
    assert data["open_recoveries"] == 1
    assert data["customers_at_risk"] == 1
    assert data["potential_revenue"] == 320000


def test_recovery_resolution_is_tenant_scoped():
    owner, tenant_id = account()
    other, other_tenant = account("other@example.com")
    with DB.begin() as db:
        job = RecoveryJob(
            tenant_id=tenant_id,
            kind="missed_call",
            status="attention",
            due_at=now(),
            encrypted_payload=encrypt({"call_sid": "CAprivate"}),
        )
        db.add(job)
        db.flush()
        job_id = job.id

    assert other.post(
        f"/api/customer-os/recovery/{job_id}/resolve",
        headers=HEADERS,
    ).status_code == 404
    assert owner.post(
        f"/api/customer-os/recovery/{job_id}/resolve",
        headers=HEADERS,
    ).status_code == 200
    with DB() as db:
        assert db.get(RecoveryJob, job_id).status == "resolved"


def test_customer_brain_keeps_sensitive_summary_encrypted():
    client, tenant_id = account()
    with DB.begin() as db:
        customer = Customer(tenant_id=tenant_id)
        db.add(customer)
        db.flush()
        customer_id = customer.id

    response = client.put(
        f"/api/customer-os/customers/{customer_id}/brain",
        json={
            "vip": True,
            "owner": "Account team",
            "risk_score": 25,
            "revenue_signal": 480000,
            "summary": "Waiting for revised renewal proposal",
            "next_best_action": "Call before Friday",
        },
        headers=HEADERS,
    )
    assert response.status_code == 200

    with DB() as db:
        state = db.get(CustomerState, customer_id)
        assert "renewal proposal" not in state.encrypted_state
        assert state.vip is True

    brain = client.get(f"/api/customer-os/customers/{customer_id}/brain")
    assert brain.status_code == 200
    assert brain.json()["summary"] == "Waiting for revised renewal proposal"
