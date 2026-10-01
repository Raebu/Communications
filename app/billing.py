"""Stripe state is always resolved from the provider, never a browser redirect."""
import hashlib
import stripe
from fastapi import HTTPException
from sqlalchemy import select
from .config import settings
from .models import Audit, DB, Tenant, now


def client():
    if not settings.stripe_key:
        raise HTTPException(503, 'Billing is not configured')
    return stripe.StripeClient(settings.stripe_key, max_network_retries=2)


def integration_identifier(seed):
    suffix = ''.join(chr(97 + (byte % 26)) for byte in hashlib.sha256(seed.encode()).digest()[:8])
    return 'raeburn_communications_' + suffix


def reconcile_subscription(db, subscription, actor='stripe'):
    tenant = db.scalar(select(Tenant).where(Tenant.stripe_customer == subscription['customer']).with_for_update())
    if not tenant:
        raise HTTPException(409, 'Unknown billing customer')
    prices = {settings.business_price: 'business', settings.connect_price: 'connect'}
    items = subscription['items']['data']
    if len(items) != 1 or items[0].get('quantity', 1) != 1:
        raise HTTPException(409, 'Subscription items require reconciliation')
    price_id = items[0]['price']['id']
    if not price_id or price_id not in prices:
        raise HTTPException(409, 'Unrecognised plan price')
    if tenant.subscription and tenant.subscription != subscription['id']:
        # Old callbacks must not overwrite a replacement subscription.
        if subscription['status'] in {'canceled', 'incomplete_expired'}:
            return tenant
        current = client().v1.subscriptions.retrieve(tenant.subscription)
        if current.status not in {'canceled', 'incomplete_expired'}:
            raise HTTPException(409, 'Multiple subscriptions require reconciliation')
    invoice = subscription.get('latest_invoice')
    paid = hasattr(invoice, 'get') and (invoice.get('status') == 'paid' or invoice.get('paid') is True)
    tenant.subscription, tenant.plan = subscription['id'], prices[price_id]
    tenant.billing_status = ('active' if paid else 'unpaid') if subscription['status'] == 'active' else subscription['status']
    tenant.billing_checked_at = now()
    tenant.checkout_sid, tenant.checkout_plan = '', ''
    db.add(Audit(tenant_id=tenant.id, actor=actor, action='billing.reconciled', detail=tenant.billing_status))
    return tenant


def subscription_from_invoice(invoice):
    # Current Stripe APIs moved subscription to parent.subscription_details.
    parent = invoice.get('parent') or {}
    details = parent.get('subscription_details') or {}
    return details.get('subscription') or invoice.get('subscription')


def periodic_reconcile():
    from datetime import timedelta
    with DB() as db:
        tenants = db.scalars(select(Tenant).where(Tenant.stripe_customer.is_not(None),
            (Tenant.billing_checked_at.is_(None)) | (Tenant.billing_checked_at < now() - timedelta(minutes=5))).limit(20)).all()
    for tenant in tenants:
        api = client()
        if tenant.subscription:
            subscription = api.v1.subscriptions.retrieve(tenant.subscription, {'expand': ['latest_invoice']})
        elif tenant.checkout_sid:
            checkout = api.v1.checkout.sessions.retrieve(tenant.checkout_sid)
            if not checkout.subscription:
                with DB.begin() as db:
                    db.get(Tenant, tenant.id).billing_checked_at = now()
                continue
            subscription = api.v1.subscriptions.retrieve(checkout.subscription, {'expand': ['latest_invoice']})
        else:
            with DB.begin() as db:
                db.get(Tenant, tenant.id).billing_checked_at = now()
            continue
        with DB.begin() as db:
            reconcile_subscription(db, subscription, actor='worker')
