from datetime import timedelta
from sqlalchemy import select
from app.models import ActionJob, Audit, Call, Conversation, DB, Message, VoiceSession, now
from app.operations import retention_one
from app.security import encrypt
from test_ai import setup_ai as setup_ai
from test_autonomy import enable_auto, inbound
from test_flows import HEADERS, customer


def test_integration_keys_scope_revocation_and_cursor_isolation(setup_ai):
    c, t, n = setup_ai
    created = c.post("/api/integration-keys", headers=HEADERS, json={"name": "CRM", "scopes": ["leads:read", "events:read"]})
    assert created.status_code == 200
    token = created.json()["token"]
    headers = {"Authorization": "Bearer " + token}
    assert c.get("/api/customer/v1/leads", headers=headers).status_code == 200
    assert c.get("/api/customer/v1/bookings", headers=headers).status_code == 403
    assert c.get("/api/customer/v1/leads").status_code == 401
    assert "token" not in c.get("/api/integration-keys").text
    with DB.begin() as db:
        other, other_t = customer("integrationother@example.com")
        event = Audit(tenant_id=other_t, actor="test", action="other.private")
        db.add(event)
        db.flush()
        event_id = event.id
    assert c.get("/api/customer/v1/events?after=" + event_id, headers=headers).status_code == 404
    assert c.delete("/api/integration-keys/" + created.json()["id"], headers=HEADERS).status_code == 200
    assert c.get("/api/customer/v1/leads", headers=headers).status_code == 401


def test_knowledge_revisions_and_finance_evidence_are_immutable(setup_ai):
    c, t, n = setup_ai
    first = {"title": "Hours", "content": "Open at 9", "approved": True}
    kid = c.post("/api/knowledge", headers=HEADERS, json=first).json()["id"]
    assert c.put("/api/knowledge/" + kid, headers=HEADERS, json={**first, "content": "Open at 10"}).status_code == 200
    versions = c.get("/api/knowledge/" + kid + "/versions").json()
    assert [v["snapshot"]["content"] for v in versions] == ["Open at 9", "Open at 10"]
    cost = {"kind": "cost", "category": "ai", "amount": 200, "period": "2026-10", "reference": "invoice_AI_1001"}
    assert c.post("/api/finance", headers=HEADERS, json=cost).status_code == 200
    assert c.post("/api/finance", headers=HEADERS, json=cost).status_code == 200
    assert c.post("/api/finance", headers=HEADERS, json={**cost, "amount": 300}).status_code == 409
    summary = c.get("/api/finance?period=2026-10").json()
    assert summary["recorded_costs"] == 200 and summary["recorded_margin"] == -200
    assert not summary["complete_ledger_verified"]


def test_action_review_never_reexecutes_and_retention_scrubs_content(setup_ai):
    c, t, n = setup_ai
    enable_auto(t)
    inbound(c, t, "Hello", "SMops")
    with DB.begin() as db:
        convo = db.scalar(select(Conversation))
        job = ActionJob(
            tenant_id=t,
            conversation_id=convo.id,
            kind="email",
            status="review",
            request_key="review-email",
            payload=encrypt({"body": "private email"}),
        )
        db.add(job)
        message = db.scalar(select(Message).where(Message.sid == "SMops"))
        message.created_at = now() - timedelta(days=100)
        db.add(Call(sid="CAold", tenant_id=t, number_id=n, destination="", reserved_minutes=1, created_at=now() - timedelta(days=40)))
        db.flush()
        db.add(
            VoiceSession(
                sid="CAold", tenant_id=t, token="old", history=encrypt({"messages": [{"role": "user", "content": "private voice"}]})
            )
        )
        db.flush()
        jid = job.id
    result = c.post(
        "/api/actions/" + jid + "/review", headers=HEADERS, json={"outcome": "verified_not_executed", "reference": "provider-review-123"}
    )
    assert result.status_code == 200
    assert (
        c.post(
            "/api/actions/" + jid + "/review", headers=HEADERS, json={"outcome": "verified_completed", "reference": "provider-review-124"}
        ).status_code
        == 409
    )
    assert retention_one()
    assert not retention_one()
    with DB() as db:
        assert db.scalar(select(Message).where(Message.sid == "SMops")).body == "[Content removed by retention policy]"
        assert db.get(VoiceSession, "CAold").history == ""
        assert db.get(ActionJob, jid).status == "cancelled"
