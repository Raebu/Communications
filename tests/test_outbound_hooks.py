import hashlib
import hmac
import json
from unittest.mock import MagicMock
import pytest
from sqlalchemy import select
from app import ai, outbound_hooks
from app.autonomy import action_one
from app.models import DB, Integration, WebhookJob
from app.security import encrypt
from test_ai import setup_ai as setup_ai
from test_autonomy import enable_auto, inbound


def test_hook_signature_public_pin_and_no_redirect(monkeypatch):
    with pytest.raises(ValueError):
        outbound_hooks.configuration("https://crm.example.test/events", "127.0.0.1", "x" * 32)
    with pytest.raises(ValueError):
        outbound_hooks.configuration("http://crm.example.test/events", "1.1.1.1", "x" * 32)
    connection = MagicMock()
    connection.getresponse.return_value.status = 302
    monkeypatch.setattr(outbound_hooks, "PinnedHTTPS", lambda host, address: connection)
    cfg = outbound_hooks.configuration("https://crm.example.test/events", "1.1.1.1", "x" * 32)
    assert outbound_hooks.deliver(cfg, "event-id", {"type": "test"}) == 302
    args = connection.request.call_args.args
    headers = args[3]
    expected = hmac.new(b"x" * 32, headers["X-Raeburn-Timestamp"].encode() + b".event-id." + args[2], hashlib.sha256).hexdigest()
    assert headers["X-Raeburn-Signature"] == "sha256=" + expected
    assert connection.request.call_count == 1
    connection.close.assert_called_once()


def test_action_event_is_durable_and_unknown_delivery_not_retried(setup_ai, monkeypatch):
    from app.models import ActionJob

    c, t, n = setup_ai
    enable_auto(t)
    with DB.begin() as db:
        db.add(
            Integration(
                tenant_id=t,
                kind="outbound_webhook",
                name="CRM",
                enabled=True,
                encrypted_config=encrypt(
                    {"url": "https://crm.example.test/events", "address": "1.1.1.1", "secret": "x" * 32, "receiver_idempotent": True}
                ),
            )
        )
    monkeypatch.setattr(
        ai, "generate", lambda *a: json.dumps({"reply": "Pass it on", "intent": "lead", "args": {"summary": "Customer enquiry"}})
    )
    inbound(c, t, "Pass this enquiry to the team", "SMhook")
    assert ai.draft_one()
    with DB() as db:
        jid = db.scalar(select(ActionJob.id))
    inbound(c, t, "CONFIRM " + jid, "SMhookconfirm")
    assert action_one()
    monkeypatch.setattr(outbound_hooks, "deliver", lambda *a: (_ for _ in ()).throw(TimeoutError()))
    assert outbound_hooks.webhook_one()
    assert not outbound_hooks.webhook_one()
    with DB() as db:
        j = db.scalar(select(WebhookJob))
        assert j.status == "review" and j.attempts == 1
    assert c.get("/api/integration-deliveries").status_code == 200
