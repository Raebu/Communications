import re
from datetime import timedelta
from sqlalchemy import select
from app import ai
from app.models import Conversation, DB, IdentityChallenge, Message
from app.security import decrypt
from test_ai import setup_ai as setup_ai
from test_autonomy import enable_auto, inbound
from test_flows import twilio_post


def code(db):
    message = db.scalar(select(Message).where(Message.sensitive_payload != ""))
    return re.search(r"code is (\d{8})", decrypt(message.sensitive_payload)["body"])[1]


def other_channel(c, t, text, sid):
    # A second approved business number is unnecessary: independent peer also requires proof.
    return twilio_post(c, "/webhooks/twilio/inbound", t, {"MessageSid": sid, "To": "+442080001001", "From": "+447700900999", "Body": text})


def test_proof_is_single_use_and_preference_is_private(setup_ai, monkeypatch):
    c, t, n = setup_ai
    enable_auto(t)
    assert inbound(c, t, "LINK", "SMproof").status_code == 200
    with DB() as db:
        token = code(db)
        assert token not in db.scalar(select(Message).where(Message.direction == "outbound")).body
    assert other_channel(c, t, "LINK " + token, "SMredeem").status_code == 200
    with DB() as db:
        threads = db.scalars(select(Conversation)).all()
        assert threads[0].customer_id == threads[1].customer_id and threads[0].customer_id
        assert db.scalar(select(IdentityChallenge)).consumed
        assert not any(token in m.body for m in db.scalars(select(Message)))
    assert inbound(c, t, "REMEMBER Prefer morning appointments", "SMpref").status_code == 200
    contexts = []
    import json

    monkeypatch.setattr(
        ai,
        "generate",
        lambda p, h: contexts.append(p.business_info) or json.dumps({"reply": "We can help.", "intent": "answer", "args": {}}),
    )
    assert other_channel(c, t, "Can you help?", "SMcross").status_code == 200
    assert ai.draft_one()
    assert "Prefer morning appointments" in contexts[0]
    assert other_channel(c, t, "LINK " + token, "SMreplay").status_code == 200
    with DB() as db:
        assert db.scalar(
            select(Message).where(Message.request_key == "ai:" + db.scalar(select(Message.id).where(Message.sid == "SMreplay")))
        ).body.startswith("Verification code is invalid")


def test_expired_proof_and_unverified_preferences_rejected(setup_ai):
    from app.models import now

    c, t, n = setup_ai
    enable_auto(t)
    inbound(c, t, "LINK", "SMexpire")
    with DB.begin() as db:
        token = code(db)
        db.scalar(select(IdentityChallenge)).expires_at = now() - timedelta(seconds=1)
    other_channel(c, t, "LINK " + token, "SMexpired")
    other_channel(c, t, "REMEMBER private preference", "SMunverified")
    with DB() as db:
        assert not any(c.customer_id for c in db.scalars(select(Conversation)))
