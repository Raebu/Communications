"""Tenant merchant Checkout: approved fixed prices and provider-verified receipts."""

from datetime import timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlparse
import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from .models import ActionJob, Audit, CatalogItem, Conversation, DB, Event, Integration, PaymentRequest, Tenant, now
from .config import settings
from .security import csrf, current_user, decrypt
from .billing import integration_identifier

router = APIRouter()


def merchant(db, tenant_id, integration_id):
    i = db.get(Integration, integration_id)
    if not i or i.tenant_id != tenant_id or i.kind != "stripe_merchant" or not i.enabled:
        raise RuntimeError("Merchant unavailable")
    cfg = decrypt(i.encrypted_config)
    if settings.environment == "production" and not cfg["key"].startswith("rk_live_"):
        raise RuntimeError("Live restricted merchant key required")
    return stripe.StripeClient(cfg["key"], max_network_retries=2), cfg


def verified_price(api, price_id):
    p = api.v1.prices.retrieve(price_id)
    if (
        not p["active"]
        or p["type"] != "one_time"
        or p["currency"] != "gbp"
        or not isinstance(p["unit_amount"], int)
        or not 0 < p["unit_amount"] <= 1000000
    ):
        raise RuntimeError("Use an active fixed GBP price at or below GBP 10,000")
    return p


class CatalogInput(BaseModel):
    integration_id: str
    name: str = Field(min_length=1, max_length=100)
    price_id: str = Field(pattern=r"^price_[A-Za-z0-9]+$")
    all_taxes_included_confirmed: bool


@router.post("/api/catalog", dependencies=[Depends(csrf)])
def add_catalog(data: CatalogInput, user=Depends(current_user)):
    from .autonomy import owner

    owner(user)
    if not data.all_taxes_included_confirmed:
        raise HTTPException(422, "Confirm the published amount includes applicable taxes and charges")
    with DB.begin() as db:
        db.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
        try:
            api, _ = merchant(db, user.tenant_id, data.integration_id)
            p = verified_price(api, data.price_id)
        except Exception:
            raise HTTPException(409, "Merchant price could not be verified")
        item = CatalogItem(
            tenant_id=user.tenant_id,
            integration_id=data.integration_id,
            name=data.name,
            price_id=data.price_id,
            amount=p["unit_amount"],
            currency=p["currency"],
            enabled=True,
        )
        db.add(item)
        db.flush()
        db.add(Audit(tenant_id=user.tenant_id, actor=user.id, action="catalog.approved", detail=item.id))
        return {"id": item.id}


@router.get("/api/catalog")
def catalog(user=Depends(current_user)):
    with DB() as db:
        return [
            {"id": p.id, "name": p.name, "amount": p.amount, "currency": p.currency, "enabled": p.enabled}
            for p in db.scalars(select(CatalogItem).where(CatalogItem.tenant_id == user.tenant_id))
        ]


class EnabledInput(BaseModel):
    enabled: bool


@router.put("/api/catalog/{item_id}", dependencies=[Depends(csrf)])
def set_catalog(item_id: str, data: EnabledInput, user=Depends(current_user)):
    from .autonomy import owner

    owner(user)
    with DB.begin() as db:
        db.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
        item = db.get(CatalogItem, item_id)
        if not item or item.tenant_id != user.tenant_id:
            raise HTTPException(404, "Product not found")
        item.enabled = data.enabled
        db.add(
            Audit(tenant_id=user.tenant_id, actor=user.id, action="catalog.enabled" if data.enabled else "catalog.disabled", detail=item.id)
        )
    return {"saved": True}


def checkout(db, t, job, payload):
    item = db.get(CatalogItem, payload["catalog_id"])
    if not item or item.tenant_id != t.id or not item.enabled or payload["amount"] != item.amount:
        raise RuntimeError("Product changed; require a fresh proposal")
    prior = db.scalar(select(PaymentRequest).where(PaymentRequest.action_id == job.id))
    if prior:
        return {"payment_id": prior.id, "url": prior.url, "status": prior.status}
    if job.created_at.replace(tzinfo=timezone.utc) < now() - timedelta(hours=23):
        raise RuntimeError("Idempotency window requires review")
    api, _ = merchant(db, t.id, item.integration_id)
    price = verified_price(api, item.price_id)
    if price["unit_amount"] != item.amount:
        raise RuntimeError("Published price changed")
    session = api.v1.checkout.sessions.create(
        {
            "mode": "payment",
            "line_items": [{"price": item.price_id, "quantity": 1}],
            "client_reference_id": job.id,
            "metadata": {"communications_tenant": t.id, "communications_action": job.id},
            "success_url": settings.public_url + "/payment-status",
            "cancel_url": settings.public_url + "/payment-status",
            "integration_identifier": integration_identifier(job.id),
            "expires_at": int((now() + timedelta(hours=1)).timestamp()),
        },
        options={"idempotency_key": "communications:" + job.id},
    )
    url = urlparse(session["url"])
    if url.scheme != "https" or url.hostname != "checkout.stripe.com" or url.username:
        raise RuntimeError("Invalid hosted payment URL")
    request = PaymentRequest(
        tenant_id=t.id,
        action_id=job.id,
        catalog_id=item.id,
        session_id=session["id"],
        url=session["url"],
        amount=item.amount,
        currency=item.currency,
    )
    db.add(request)
    db.flush()
    return {"payment_id": request.id, "url": request.url, "status": "open"}


def process_webhook(integration_id, raw, signature):
    with DB.begin() as db:
        i = db.get(Integration, integration_id)
        if not i or i.kind != "stripe_merchant":
            raise HTTPException(404, "Merchant not found")
        # Continue reconciling already-created payments after integration disablement.
        cfg = decrypt(i.encrypted_config)
        try:
            event = stripe.Webhook.construct_event(raw, signature, cfg["webhook_secret"])
        except (ValueError, stripe.SignatureVerificationError):
            raise HTTPException(403, "Invalid signature")
        if event["type"] not in {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
            "checkout.session.async_payment_failed",
            "checkout.session.expired",
        }:
            return {"received": True}
        t = db.scalar(select(Tenant).where(Tenant.id == i.tenant_id).with_for_update())
        if db.get(Event, event["id"]):
            return {"received": True}
        api = stripe.StripeClient(cfg["key"], max_network_retries=2)
        session = api.v1.checkout.sessions.retrieve(event["data"]["object"]["id"])
        pay = db.scalar(
            select(PaymentRequest).where(PaymentRequest.session_id == session["id"], PaymentRequest.tenant_id == t.id).with_for_update()
        )
        if not pay:
            # Ignore unrelated merchant checkouts; never attach them by customer email/phone.
            return {"received": True}
        item = db.get(CatalogItem, pay.catalog_id)
        if (
            item.integration_id != i.id
            or session["metadata"].get("communications_action") != pay.action_id
            or session["metadata"].get("communications_tenant") != t.id
            or session["mode"] != "payment"
            or session["currency"] != pay.currency
            or session["amount_total"] != pay.amount
        ):
            raise HTTPException(409, "Payment reconciliation mismatch")
        if session["payment_status"] == "paid" and session["status"] == "complete":
            if pay.status != "paid":
                pay.status = "paid"
                j = db.get(ActionJob, pay.action_id)
                c = db.get(Conversation, j.conversation_id)
                if c.channel != "voice":
                    from .autonomy import queue_reply

                    m = SimpleNamespace(id=pay.id, tenant_id=t.id, number_id=c.number_id, channel=c.channel, peer=c.peer, body="receipt")
                    try:
                        queue_reply(
                            db, t, m, "Payment confirmed: " + item.name + " GBP " + f"{pay.amount / 100:.2f}" + ". Reference " + pay.id
                        )
                    except RuntimeError:
                        db.add(Audit(tenant_id=t.id, actor="stripe", action="payment.receipt.blocked", detail=pay.id))
        elif session["status"] == "expired" and pay.status != "paid":
            pay.status = "expired"
        db.add(Event(id=event["id"], tenant_id=t.id, kind=event["type"]))
        db.add(Audit(tenant_id=t.id, actor="stripe", action="payment.reconciled", detail=pay.id + ":" + pay.status))
    return {"received": True}


@router.post("/webhooks/stripe/merchant/{integration_id}")
async def webhook(integration_id: str, request: Request):
    return await run_in_threadpool(process_webhook, integration_id, await request.body(), request.headers.get("stripe-signature", ""))


@router.get("/api/payments")
def payments(user=Depends(current_user)):
    with DB() as db:
        return [
            {"id": p.id, "amount": p.amount, "currency": p.currency, "status": p.status, "created_at": p.created_at}
            for p in db.scalars(
                select(PaymentRequest)
                .where(PaymentRequest.tenant_id == user.tenant_id)
                .order_by(PaymentRequest.created_at.desc())
                .limit(200)
            )
        ]
