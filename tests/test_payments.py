import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock
from sqlalchemy import select
from app import ai, payments
from app.models import ActionJob, DB, Integration, PaymentRequest
from app.security import encrypt
from test_ai import setup_ai as setup_ai
from test_autonomy import enable_auto, inbound
from test_flows import HEADERS


def merchant(t, monkeypatch):
    api = MagicMock()
    api.v1.prices.retrieve.return_value = {
        "id": "price_approved",
        "active": True,
        "type": "one_time",
        "currency": "gbp",
        "unit_amount": 2500,
    }
    api.v1.checkout.sessions.create.return_value = {"id": "cs_checkout", "url": "https://checkout.stripe.com/c/pay/cs_checkout"}
    monkeypatch.setattr(payments.stripe, "StripeClient", lambda *a, **kw: api)
    with DB.begin() as db:
        i = Integration(
            tenant_id=t,
            kind="stripe_merchant",
            name="Merchant",
            enabled=True,
            encrypted_config=encrypt({"key": "rk_test_merchant", "webhook_secret": "whsec_test", "account_id": "acct_merchant"}),
        )
        db.add(i)
        db.flush()
        return api, i.id


def event(body):
    raw = json.dumps(body)
    timestamp = str(int(time.time()))
    signature = hmac.new(b"whsec_test", (timestamp + "." + raw).encode(), hashlib.sha256).hexdigest()
    return raw, {"stripe-signature": "t=" + timestamp + ",v1=" + signature}


def test_confirmed_checkout_and_verified_paid_receipt(setup_ai, monkeypatch):
    from app.autonomy import action_one

    c, t, n = setup_ai
    enable_auto(t)
    api, iid = merchant(t, monkeypatch)
    added = c.post(
        "/api/catalog",
        headers=HEADERS,
        json={"integration_id": iid, "name": "Consultation", "price_id": "price_approved", "all_taxes_included_confirmed": True},
    )
    assert added.status_code == 200
    pid = added.json()["id"]
    monkeypatch.setattr(
        ai, "generate", lambda *a: json.dumps({"reply": "I can provide a link.", "intent": "paymentlink", "args": {"catalog_id": pid}})
    )
    inbound(c, t, "Please send a payment link", "SMpay")
    assert ai.draft_one()
    with DB() as db:
        job = db.scalar(select(ActionJob))
        jid = job.id
        assert not db.scalar(select(PaymentRequest))
    inbound(c, t, "CONFIRM " + jid, "SMpayconfirm")
    assert action_one()
    assert not action_one()
    assert api.v1.checkout.sessions.create.call_count == 1
    params = api.v1.checkout.sessions.create.call_args.args[0]
    assert params["line_items"] == [{"price": "price_approved", "quantity": 1}]
    assert "payment_method_types" not in params
    assert "automatic_tax" not in params
    with DB() as db:
        assert db.scalar(select(PaymentRequest)).status == "open"
    api.v1.checkout.sessions.retrieve.return_value = {
        "id": "cs_checkout",
        "metadata": {"communications_tenant": t, "communications_action": jid},
        "mode": "payment",
        "currency": "gbp",
        "amount_total": 2500,
        "payment_status": "unpaid",
        "status": "complete",
    }
    raw, headers = event({"id": "evt_checkout", "type": "checkout.session.completed", "data": {"object": {"id": "cs_checkout"}}})
    assert c.post("/webhooks/stripe/merchant/" + iid, content=raw, headers=headers).status_code == 200
    with DB() as db:
        assert db.scalar(select(PaymentRequest)).status == "open"
    api.v1.checkout.sessions.retrieve.return_value["payment_status"] = "paid"
    raw, headers = event({"id": "evt_paid", "type": "checkout.session.async_payment_succeeded", "data": {"object": {"id": "cs_checkout"}}})
    assert c.post("/webhooks/stripe/merchant/" + iid, content=raw, headers=headers).status_code == 200
    assert c.post("/webhooks/stripe/merchant/" + iid, content=raw, headers=headers).status_code == 200
    with DB() as db:
        assert db.scalar(select(PaymentRequest)).status == "paid"
    assert c.post("/webhooks/stripe/merchant/" + iid, content=raw, headers={"stripe-signature": "invalid"}).status_code == 403


def test_catalog_price_approval_and_tenant_isolation(setup_ai, monkeypatch):
    from test_flows import customer

    c, t, n = setup_ai
    api, iid = merchant(t, monkeypatch)
    data = {"integration_id": iid, "name": "Product", "price_id": "price_approved", "all_taxes_included_confirmed": False}
    assert c.post("/api/catalog", headers=HEADERS, json=data).status_code == 422
    data["all_taxes_included_confirmed"] = True
    other, _ = customer("merchantother@example.com")
    assert other.post("/api/catalog", headers=HEADERS, json=data).status_code == 409
    api.v1.prices.retrieve.return_value["unit_amount"] = None
    assert c.post("/api/catalog", headers=HEADERS, json=data).status_code == 409
