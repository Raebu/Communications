import json
from datetime import timedelta
from sqlalchemy import select
from app import ai
from app.autonomy import action_one, slots
from app.models import AIJob, AIProfile, ActionJob, Booking, Conversation, DB, Department, Knowledge, Message, now
from test_ai import setup_ai as setup_ai
from test_flows import HEADERS, twilio_post


def enable_auto(t):
    with DB.begin() as db:
        db.get(AIProfile, t).autonomous = True


def inbound(c, t, body, sid):
    return twilio_post(c, "/webhooks/twilio/inbound", t, {"MessageSid": sid, "To": "+442080001001", "From": "+447700900123", "Body": body})


def test_automatic_reply_replay_and_pause(setup_ai, monkeypatch):
    c, t, n = setup_ai
    enable_auto(t)
    monkeypatch.setattr(ai, "generate", lambda *a: json.dumps({"reply": "We offer bookkeeping.", "intent": "answer", "args": {}}))
    assert inbound(c, t, "What do you do?", "SMauto").status_code == 200
    assert inbound(c, t, "What do you do?", "SMauto").status_code == 200
    with DB() as db:
        assert len(db.scalars(select(AIJob)).all()) == 1
    assert ai.draft_one()
    with DB() as db:
        outbound = db.scalar(select(Message).where(Message.direction == "outbound"))
        assert outbound.body == "AI receptionist: We offer bookkeeping."
        assert outbound.status == "queued"
    assert inbound(c, t, "And more?", "SMnext").status_code == 200
    with DB.begin() as db:
        db.get(AIProfile, t).paused = True
    assert ai.draft_one()
    with DB() as db:
        assert len(db.scalars(select(Message).where(Message.direction == "outbound")).all()) == 1


def test_handoff_and_knowledge_isolation(setup_ai, monkeypatch):
    c, t, n = setup_ai
    enable_auto(t)
    with DB.begin() as db:
        db.add(Knowledge(tenant_id=t, title="Approved", content="approved fact", approved=True))
        db.add(Knowledge(tenant_id=t, title="Draft", content="secret draft", approved=False))
        db.add(Knowledge(tenant_id=t, title="Expired", content="stale fact", approved=True, expires_at=now() - timedelta(days=1)))
    contexts = []
    monkeypatch.setattr(
        ai,
        "generate",
        lambda p, h: contexts.append(p.business_info) or json.dumps({"reply": "A person will help.", "intent": "handoff", "args": {}}),
    )
    assert inbound(c, t, "Complex question", "SMhand").status_code == 200
    assert ai.draft_one()
    with DB() as db:
        assert db.scalar(select(Conversation)).mode == "human"
        assert db.scalar(select(Message).where(Message.direction == "outbound"))
    assert "approved fact" in contexts[0] and "secret draft" not in contexts[0] and "stale fact" not in contexts[0]
    assert inbound(c, t, "Hello again", "SMhuman").status_code == 200
    assert not ai.draft_one()


def test_booking_needs_exact_confirmation_and_prevents_conflicts(setup_ai, monkeypatch):
    c, t, n = setup_ai
    enable_auto(t)
    with DB.begin() as db:
        d = Department(tenant_id=t, name="Consulting", bookings_enabled=True)
        db.add(d)
        db.flush()
        did = d.id
        when = slots(db, d)[0]
    monkeypatch.setattr(
        ai,
        "generate",
        lambda *a: json.dumps({"reply": "We can book.", "intent": "book", "args": {"department_id": did, "starts_at": when}}),
    )
    assert inbound(c, t, "Please book a consultation", "SMbook").status_code == 200
    assert ai.draft_one()
    with DB() as db:
        job = db.scalar(select(ActionJob))
        jid = job.id
        assert job.status == "awaiting_confirmation"
        assert not db.scalars(select(Booking)).all()
    assert inbound(c, t, "CONFIRM " + jid, "SMconfirm").status_code == 200
    assert action_one()
    with DB() as db:
        assert db.get(ActionJob, jid).status == "completed"
        assert len(db.scalars(select(Booking)).all()) == 1
        assert when not in slots(db, db.get(Department, did))
    assert not action_one()


def test_email_content_confirmation_and_human_takeover(setup_ai, monkeypatch):
    from app.config import settings
    from app.models import EmailJob
    from app.worker import send_one

    c, t, n = setup_ai
    enable_auto(t)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "email_from", "support@example.com")
    monkeypatch.setattr(
        ai,
        "generate",
        lambda *a: json.dumps(
            {
                "reply": "I can email it.",
                "intent": "email",
                "args": {"email": "customer@example.com", "subject": "Information", "body": "We offer bookkeeping."},
            }
        ),
    )
    assert inbound(c, t, "Email me your services", "SMmail").status_code == 200
    assert ai.draft_one()
    with DB() as db:
        job = db.scalar(select(ActionJob))
        jid = job.id
        preview = db.scalar(select(Message).where(Message.direction == "outbound")).body
        assert "customer@example.com" in preview and "We offer bookkeeping." in preview
        assert not db.scalars(select(EmailJob)).all()
        cid = db.scalar(select(Conversation)).id
    assert c.put("/api/conversations/" + cid + "/mode", json={"mode": "human"}, headers=HEADERS).status_code == 200
    assert send_one()
    with DB() as db:
        assert db.scalar(select(Message).where(Message.direction == "outbound")).error == "automation_paused"
    assert c.put("/api/conversations/" + cid + "/mode", json={"mode": "ai"}, headers=HEADERS).status_code == 200
    assert inbound(c, t, "CONFIRM " + jid, "SMmailconfirm").status_code == 200
    assert action_one()
    with DB() as db:
        assert len(db.scalars(select(EmailJob)).all()) == 1
        assert "queued" in db.get(ActionJob, jid).receipt


def test_cancel_only_customer_owned_booking(setup_ai, monkeypatch):
    c, t, n = setup_ai
    enable_auto(t)
    with DB.begin() as db:
        d = Department(tenant_id=t, name="Consulting", bookings_enabled=True)
        db.add(d)
        db.flush()
        b = Booking(
            tenant_id=t,
            department_id=d.id,
            peer="+447700900123",
            starts_at=now() + timedelta(days=1),
            ends_at=now() + timedelta(days=1, minutes=30),
            request_key="old",
        )
        db.add(b)
        db.flush()
        bid = b.id
    monkeypatch.setattr(ai, "generate", lambda *a: json.dumps({"reply": "I can cancel.", "intent": "cancel", "args": {"booking_id": bid}}))
    assert inbound(c, t, "Please cancel my appointment", "SMcancel").status_code == 200
    assert ai.draft_one()
    with DB() as db:
        jid = db.scalar(select(ActionJob)).id
    assert inbound(c, t, "CONFIRM " + jid, "SMcancelconfirm").status_code == 200
    assert action_one()
    with DB() as db:
        assert db.get(Booking, bid).status == "cancelled"


def test_evaluation_reserves_quota_before_model_calls(setup_ai, monkeypatch):
    from app.config import settings

    c, t, n = setup_ai
    seen = []
    monkeypatch.setattr(ai, "generate", lambda *a: seen.append(a) or "Bookkeeping services.")
    monkeypatch.setattr(settings, "ai_daily_requests", 1)
    payload = {"cases": [{"question": "Services?"}, {"question": "Hours?"}]}
    assert c.post("/api/ai/evaluate", json=payload, headers=HEADERS).status_code == 429
    assert not seen
    payload = {"cases": [{"question": "Services?", "expected_terms": ["bookkeeping"]}]}
    r = c.post("/api/ai/evaluate", json=payload, headers=HEADERS)
    assert r.status_code == 200 and r.json()["results"][0]["passed"]


def test_upgrade_preserves_existing_ai_and_number_defaults():
    import os
    import subprocess
    import sys
    import tempfile
    from sqlalchemy import create_engine, text

    path = tempfile.mktemp(suffix=".db")
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + path}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "0003"], env=env, check=True, capture_output=True)
    engine = create_engine("sqlite:///" + path)
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO tenants (id,name,legal_name,address,registration_number,terms_version,status,credentials,bundle_sid,address_sid,bundle_type,billing_status,plan,spend_limit,checkout_sid,checkout_plan,created_at) VALUES ('t','Old Co','','','','draft','pending','','','','','unpaid','connect',2000,'','','2026-10-01')"
            )
        )
        db.execute(
            text(
                "INSERT INTO numbers (id,tenant_id,phone,sid,sms,voice,whatsapp,rcs,forwarding) VALUES ('n','t','+442080001001','PNold',1,1,'not_registered','not_registered','')"
            )
        )
        db.execute(text("INSERT INTO ai_profiles (tenant_id,enabled,voice_enabled,business_info,greeting) VALUES ('t',0,0,'','Hello')"))
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True, capture_output=True)
    with engine.connect() as db:
        assert db.execute(text("SELECT autonomous,paused,language FROM ai_profiles")).one() == (0, 0, "en-GB")
        assert db.execute(text("SELECT whatsapp_sender,rcs_sender,rcs_service_sid FROM numbers")).one() == ("", "", "")
