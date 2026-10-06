import hashlib
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Literal
from xml.sax.saxutils import escape
import stripe
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from twilio.request_validator import RequestValidator
from .config import settings
from .integrations import router as integrations_router
from .channels import enabled as channel_enabled
from .ai import router as ai_router, start_voice, voice_turn, configured as ai_configured
from .models import AIProfile, Conversation, CompanyVerification
from .autonomy import router as autonomy_router, inbound_ai
from .relay import router as relay_router
from .payments import router as payments_router
from .operations import router as operations_router
from .quality import router as quality_router
from .knowledge_import import router as knowledge_import_router
from .outbound_hooks import router as outbound_hooks_router
from .models import Call, WorkerHeartbeat, Audit, Base, EmailJob, DB, Event, Message, Number, Order, Session, Suppression, Tenant, User, engine, now
from .providers import create_subaccount, parent_client, tenant_client
from .security import csrf, current_user, decrypt, encrypt, hash_password, rate_limit, verify_password, verify_totp
from .worker import within_budget
from .accounts import router as accounts_router, queue_action
from .company_verification import router as company_verification_router, invalidate, require_company_verified
from .billing import client as billing_client, integration_identifier, reconcile_subscription, subscription_from_invoice


@asynccontextmanager
async def lifespan(app):
    settings.validate()
    if settings.environment != "production":
        Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Raeburn Communications", version="0.5.0", lifespan=lifespan)
app.include_router(accounts_router)
app.include_router(company_verification_router)
app.include_router(ai_router)
app.include_router(autonomy_router)

app.include_router(relay_router)
app.include_router(integrations_router)
app.include_router(payments_router)
app.include_router(operations_router)
app.include_router(quality_router)
app.include_router(knowledge_import_router)
app.include_router(outbound_hooks_router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def security_headers(request, call_next):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 65536:
            return Response(status_code=413)
        body.extend(chunk)
    request._body = bytes(body)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; frame-ancestors 'none'"
    )
    response.headers["Cache-Control"] = "no-store"
    if settings.environment == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/")
def home():
    return FileResponse("app/static/index.html")


@app.get("/health")
def health():
    with DB() as db:
        db.execute(select(1))
    return {"status": "ok", "version": app.version}


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    totp: str = Field(default="", max_length=6)


class Registration(Credentials):
    company: str = Field(min_length=2, max_length=200)
    accept_terms: bool


@app.post("/api/register", dependencies=[Depends(csrf)])
def register(data: Registration, request: Request):
    if not settings.registration_enabled:
        raise HTTPException(403, "Registration is closed while launch checks are completed")
    rate_limit("register:" + request.client.host, 5)
    if not data.accept_terms:
        raise HTTPException(422, "Accept the service terms")
    try:
        with DB.begin() as db:
            tenant = Tenant(name=data.company, terms_version=settings.terms_version or "2026-10-draft")
            db.add(tenant)
            db.flush()
            user = User(tenant_id=tenant.id, email=str(data.email).lower(), password=hash_password(data.password))
            db.add(user)
            db.flush()
            if settings.smtp_host and settings.encryption_key:
                queue_action(db, user, "verify")
    except IntegrityError:
        raise HTTPException(409, "Unable to create this account")
    return {"status": "created"}


@app.post("/api/login", dependencies=[Depends(csrf)])
def login(data: Credentials, request: Request, response: Response):
    rate_limit("login:" + request.client.host, 10)
    with DB.begin() as db:
        user = db.scalar(select(User).where(User.email == str(data.email).lower()).with_for_update())
        if not user or not verify_password(data.password, user.password):
            raise HTTPException(401, "Invalid email or password")
        if user.mfa_enabled:
            step = verify_totp(decrypt(user.mfa_secret)["secret"], data.totp, user.mfa_counter)
            if step is None:
                raise HTTPException(401, "Valid authenticator code required")
            user.mfa_counter = step
        token = secrets.token_urlsafe(48)
        db.add(
            Session(
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                user_id=user.id,
                mfa_authenticated=user.mfa_enabled,
                expires_at=now() + timedelta(hours=12),
            )
        )
        response.set_cookie("session", token, httponly=True, secure=settings.environment == "production", samesite="strict", max_age=43200)
    return {"status": "signed_in"}


@app.post("/api/logout", dependencies=[Depends(csrf)])
def logout(request: Request, response: Response, user=Depends(current_user)):
    with DB.begin() as db:
        session = db.get(Session, hashlib.sha256(request.cookies["session"].encode()).hexdigest())
        db.delete(session)
    response.delete_cookie("session")
    return {"status": "signed_out"}


def require_owner(user):
    if user.role != "owner":
        raise HTTPException(403, "Owner permission required")


def audit(db, user, action, detail=""):
    db.add(Audit(tenant_id=user.tenant_id, actor=user.id, action=action, detail=detail))


@app.get("/api/me")
def me(user=Depends(current_user)):
    with DB() as db:
        t = db.get(Tenant, user.tenant_id)
        return {
            "email": user.email,
            "role": user.role,
            "platform_admin": user.platform_admin,
            "email_verified": user.email_verified,
            "mfa_enabled": user.mfa_enabled,
            "tenant": {
                "id": t.id,
                "name": t.name,
                "legal_name": t.legal_name,
                "address": t.address,
                "registration_number": t.registration_number,
                "status": t.status,
                "billing_status": t.billing_status,
                "plan": t.plan,
                "connected": bool(t.twilio_sid),
            },
        }


class Profile(BaseModel):
    legal_name: str = Field(min_length=2, max_length=200)
    address: str = Field(min_length=10, max_length=1000)
    registration_number: str = Field(max_length=80)


@app.put("/api/profile", dependencies=[Depends(csrf)])
def profile(data: Profile, user=Depends(current_user)):
    require_owner(user)
    with DB.begin() as db:
        t = db.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
        changed = any(getattr(t, key) != value for key, value in data.model_dump().items())
        for key, value in data.model_dump().items():
            setattr(t, key, value)
        if changed:
            invalidate(db, t)
        # Changing legal identity requires renewed approval.
        if changed:
            t.status, t.bundle_sid = "pending", ""
        audit(db, user, "profile.updated")
        if changed and settings.smtp_host and settings.encryption_key:
            sender = settings.verification_email_from or settings.email_from
            db.add(EmailJob(recipient=user.email, encrypted_payload=encrypt({
                "from": sender,
                "subject": "Your company details are with us — Raeburn Connect",
                "body": "Thank you for sending your company details. They are now awaiting review. "
                        "You can check your progress in your account, and we will contact you if we need anything else.\n\n"
                        + settings.public_url + "/",
            })))
            if settings.company_review_email:
                db.add(EmailJob(recipient=settings.company_review_email, encrypted_payload=encrypt({
                    "from": sender,
                    "subject": "Company review needed — Raeburn Connect",
                    "body": "A company has submitted updated details for review. "
                            "Sign in to the customer review dashboard to check the submission.\n\n"
                            + settings.public_url + "/\n\nCompany reference: " + str(t.id),
                })))
    return {"status": "pending_review"}


class Search(BaseModel):
    type: Literal["Local", "Mobile", "TollFree"] = "Local"
    contains: str = Field(default="", max_length=12, pattern=r"^[0-9]*$")


@app.get("/api/numbers/search")
def search(type: Literal["Local", "Mobile", "TollFree"] = "Local", contains: str = "", user=Depends(current_user)):
    if len(contains) > 12 or (contains and not contains.isascii()) or (contains and not contains.isdigit()):
        raise HTTPException(422, "Search accepts up to 12 digits")
    query = Search(type=type, contains=contains)
    rate_limit("search:" + user.tenant_id, 15)
    available = getattr(
        parent_client().available_phone_numbers("GB"), {"Local": "local", "Mobile": "mobile", "TollFree": "toll_free"}[query.type]
    )
    numbers = available.list(contains=query.contains or None, limit=12)
    return [
        {
            "phone": n.phone_number,
            "name": n.friendly_name,
            "sms": n.capabilities.get("SMS", False),
            "voice": n.capabilities.get("voice", False),
            "type": query.type,
        }
        for n in numbers
    ]


@app.get("/api/numbers")
def numbers(user=Depends(current_user)):
    with DB() as db:
        rows = db.scalars(select(Number).where(Number.tenant_id == user.tenant_id)).all()
        return [
            {"id": n.id, "phone": n.phone, "sms": n.sms, "voice": n.voice, "forwarding": n.forwarding, "whatsapp": n.whatsapp, "rcs": n.rcs}
            for n in rows
        ]


class Purchase(BaseModel):
    phone: str = Field(pattern=r"^\+44\d{9,10}$")
    type: Literal["Local", "Mobile", "TollFree"]
    request_key: str = Field(min_length=16, max_length=100)


@app.post("/api/orders", dependencies=[Depends(csrf)])
def purchase(data: Purchase, user=Depends(current_user)):
    require_owner(user)
    rate_limit("purchase:" + user.tenant_id, 5)
    try:
        with DB.begin() as db:
            t = db.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
            existing = db.scalar(select(Order).where(Order.tenant_id == t.id, Order.request_key == data.request_key))
            if existing:
                if existing.phone != data.phone or existing.number_type != data.type:
                    raise HTTPException(409, "Idempotency key belongs to a different order")
                return {"id": existing.id, "status": existing.status}
            if t.status != "approved" or not t.bundle_sid or t.bundle_type != data.type:
                raise HTTPException(409, "Approved regulatory bundle for this number type required")
            if t.billing_status != "active":
                raise HTTPException(402, "Active paid subscription required")
            if not t.twilio_sid:
                raise HTTPException(409, "Communications account must be connected")
            if db.scalar(select(Number).where(Number.tenant_id == t.id)) or db.scalar(
                select(Order).where(Order.tenant_id == t.id, Order.status.in_(["queued", "processing", "review"]))
            ):
                winner = db.scalar(select(Order).where(Order.tenant_id == t.id, Order.request_key == data.request_key))
                if winner and winner.phone == data.phone and winner.number_type == data.type:
                    return {"id": winner.id, "status": winner.status}
                raise HTTPException(409, "One number per subscription; contact support for additional numbers")
            order = Order(tenant_id=t.id, phone=data.phone, number_type=data.type, request_key=data.request_key)
            db.add(order)
            db.flush()
            audit(db, user, "number.order.created", order.id)
            return {"id": order.id, "status": order.status}
    except IntegrityError:
        with DB() as db:
            existing = db.scalar(select(Order).where(Order.tenant_id == user.tenant_id, Order.request_key == data.request_key))
            if existing and existing.phone == data.phone and existing.number_type == data.type:
                return {"id": existing.id, "status": existing.status}
        raise HTTPException(409, "Conflicting activation request")


@app.get("/api/orders")
def orders(user=Depends(current_user)):
    with DB() as db:
        return [
            {"id": o.id, "phone": o.phone, "status": o.status, "error": o.error}
            for o in db.scalars(select(Order).where(Order.tenant_id == user.tenant_id).order_by(Order.created_at.desc())).all()
        ]


class Checkout(BaseModel):
    plan: Literal["business", "connect", "ai"]


@app.post("/api/billing/checkout", dependencies=[Depends(csrf)])
def checkout(data: Checkout, user=Depends(current_user)):
    require_owner(user)
    price = {"business": settings.business_price, "connect": settings.connect_price, "ai": settings.ai_price}[data.plan]
    if data.plan == "ai":
        raise HTTPException(409, "AI plan is not available until AI and booking integrations pass launch checks")
    if not settings.stripe_key or not price:
        raise HTTPException(503, "Billing is not configured")
    stripe_client = billing_client()
    with DB.begin() as db:
        t = db.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
        require_company_verified(db, t)
        if t.billing_status == "active":
            raise HTTPException(409, "Manage your existing subscription in billing portal")
        if t.status != "approved":
            raise HTTPException(409, "Complete regulatory review before subscribing")
        if t.checkout_sid:
            prior = stripe_client.v1.checkout.sessions.retrieve(t.checkout_sid)
            if prior.status == "open":
                if t.checkout_plan != data.plan:
                    raise HTTPException(409, "An existing checkout is open for a different plan; complete or expire it first")
                return {"url": prior.url}
            if prior.status == "complete":
                raise HTTPException(409, "Payment is processing; wait for subscription confirmation")
        if not t.stripe_customer:
            customer = stripe_client.v1.customers.create({"email": user.email, "name": t.name}, {"idempotency_key": "customer:" + t.id})
            t.stripe_customer = customer.id
        session = stripe_client.v1.checkout.sessions.create(
            {
                "customer": t.stripe_customer,
                "mode": "subscription",
                "line_items": [{"price": price, "quantity": 1}],
                "client_reference_id": t.id,
                "integration_identifier": integration_identifier(t.id + data.plan + (t.checkout_sid or "initial")),
                "success_url": settings.public_url + "/?billing=success",
                "cancel_url": settings.public_url + "/?billing=cancel",
            },
            {"idempotency_key": "checkout:" + t.id + ":" + data.plan + ":" + (t.checkout_sid or "initial")},
        )
        t.checkout_sid, t.checkout_plan = session.id, data.plan
        audit(db, user, "billing.checkout.created", session.id)
        return {"url": session.url}


@app.post("/api/billing/portal", dependencies=[Depends(csrf)])
def portal(user=Depends(current_user)):
    require_owner(user)
    if not settings.stripe_key:
        raise HTTPException(503, "Billing is not configured")
    stripe_client = billing_client()
    with DB() as db:
        t = db.get(Tenant, user.tenant_id)
        if not t.stripe_customer:
            raise HTTPException(409, "No billing account")
        return {
            "url": stripe_client.v1.billing_portal.sessions.create({"customer": t.stripe_customer, "return_url": settings.public_url}).url
        }


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    if not settings.stripe_key or not settings.stripe_webhook_secret:
        raise HTTPException(503, "Billing webhook is not configured")
    raw = await request.body()
    try:
        event = stripe.Webhook.construct_event(raw, request.headers.get("stripe-signature", ""), settings.stripe_webhook_secret)
    except (ValueError, stripe.SignatureVerificationError):
        raise HTTPException(403, "Invalid signature")
    obj = event["data"]["object"]
    kind = event["type"]
    stripe_client = billing_client()
    subscription_id = None
    if kind.startswith("customer.subscription."):
        subscription_id = obj["id"]
    elif kind in {"invoice.paid", "invoice.payment_failed"}:
        subscription_id = subscription_from_invoice(obj)
    elif kind in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        checkout = stripe_client.v1.checkout.sessions.retrieve(obj["id"])
        subscription_id = checkout.subscription
    if not subscription_id:
        return {"received": True}
    subscription = stripe_client.v1.subscriptions.retrieve(subscription_id, {"expand": ["latest_invoice"]})
    with DB.begin() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.stripe_customer == subscription["customer"]).with_for_update())
        if not tenant:
            raise HTTPException(409, "Unknown billing customer")
        if db.get(Event, event["id"]):
            return {"received": True}
        reconcile_subscription(db, subscription)
        db.add(Event(id=event["id"], tenant_id=tenant.id, kind=kind))
    return {"received": True}


@app.get("/api/threads")
def threads(user=Depends(current_user)):
    with DB() as db:
        rows = db.scalars(select(Message).where(Message.tenant_id == user.tenant_id).order_by(Message.created_at.desc()).limit(1000)).all()
        result = {}
        for m in rows:
            key = (m.number_id, m.channel, m.peer)
            if key not in result:
                result[key] = {
                    "number_id": m.number_id,
                    "channel": m.channel,
                    "peer": m.peer,
                    "preview": m.body,
                    "at": m.created_at.isoformat(),
                }
                c = db.scalar(
                    select(Conversation).where(
                        Conversation.tenant_id == user.tenant_id,
                        Conversation.number_id == m.number_id,
                        Conversation.peer == m.peer,
                        Conversation.channel == m.channel,
                    )
                )
                if c:
                    result[key].update(conversation_id=c.id, mode=c.mode, reason=c.reason)
        return list(result.values())


@app.get("/api/messages")
def messages(number_id: str, peer: str, channel: Literal["sms", "whatsapp", "rcs"] = "sms", user=Depends(current_user)):
    with DB() as db:
        rows = db.scalars(
            select(Message)
            .where(Message.tenant_id == user.tenant_id, Message.number_id == number_id, Message.peer == peer, Message.channel == channel)
            .order_by(Message.created_at.desc())
            .limit(200)
        ).all()
        return [
            {"id": m.id, "body": m.body, "direction": m.direction, "status": m.status, "at": m.created_at.isoformat()}
            for m in reversed(rows)
        ]


class Send(BaseModel):
    number_id: str
    peer: str = Field(pattern=r"^\+[1-9]\d{7,14}$")
    body: str = Field(min_length=1, max_length=1600)
    request_key: str = Field(min_length=16, max_length=100)
    channel: Literal["sms", "whatsapp", "rcs"] = "sms"
    consent_confirmed: bool


@app.post("/api/messages", dependencies=[Depends(csrf)])
def send(data: Send, user=Depends(current_user)):
    rate_limit("send:" + user.tenant_id, 20)
    if not data.consent_confirmed:
        raise HTTPException(422, "Confirm permission to contact this recipient")
    with DB.begin() as db:
        t = db.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
        n = db.scalar(select(Number).where(Number.id == data.number_id, Number.tenant_id == t.id))
        if not n:
            raise HTTPException(404, "Number not found")
        if not channel_enabled(n, data.channel) or t.plan == "business" or t.status != "approved" or t.billing_status != "active":
            raise HTTPException(409, "SMS is not enabled for this account")
        if not data.peer.startswith("+44"):
            raise HTTPException(409, "International messaging is disabled at launch")
        if db.scalar(select(Suppression).where(Suppression.tenant_id == t.id, Suppression.peer == data.peer)):
            raise HTTPException(409, "Recipient opted out")
        existing = db.scalar(select(Message).where(Message.tenant_id == t.id, Message.request_key == data.request_key))
        if existing:
            if existing.body != data.body or existing.peer != data.peer or existing.number_id != n.id or existing.channel != data.channel:
                raise HTTPException(409, "Idempotency key belongs to another message")
            return {"id": existing.id, "status": existing.status}
        # Provider totals can lag. Also apply a strict local daily segment cap.
        from datetime import datetime, timezone

        day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        daily = db.scalars(
            select(Message).where(Message.tenant_id == t.id, Message.direction == "outbound", Message.created_at >= day)
        ).all()
        if sum(message_units(m) for m in daily) + segment_count(data.body) > 100:
            raise HTTPException(429, "Daily SMS allowance reached")
        month = day.replace(day=1)
        monthly = db.scalars(
            select(Message).where(Message.tenant_id == t.id, Message.direction == "outbound", Message.created_at >= month)
        ).all()
        if sum(message_units(m) for m in monthly) + segment_count(data.body) > settings.sms_monthly_segments:
            raise HTTPException(429, "Monthly SMS allowance reached")
        m = Message(
            tenant_id=t.id,
            number_id=n.id,
            peer=data.peer,
            direction="outbound",
            body=data.body,
            segment_units=segment_count(data.body),
            channel=data.channel,
            request_key=data.request_key,
        )
        db.add(m)
        db.flush()
        from .autonomy import conversation

        thread = conversation(db, m)
        thread.mode, thread.assigned_to, thread.reason = "human", user.id, "staff_reply"
        audit(db, user, "message.queued", m.id)
        return {"id": m.id, "status": m.status}


def message_units(message):
    return message.segment_units or segment_count(message.body)


def segment_count(body):
    # Conservative UCS-2 bound avoids undercounting non-GSM text and emoji.
    units = len(body.encode("utf-16-le")) // 2
    return 1 if units <= 70 else (units + 66) // 67


class Forwarding(BaseModel):
    destination: str = Field(default="", pattern=r"^(\+44(?:[12]\d{9}|7[1-57-9]\d{8}))?$")


@app.put("/api/numbers/{number_id}/forwarding", dependencies=[Depends(csrf)])
def forwarding(number_id: str, data: Forwarding, user=Depends(current_user)):
    require_owner(user)
    with DB.begin() as db:
        n = db.scalar(select(Number).where(Number.id == number_id, Number.tenant_id == user.tenant_id))
        if not n or not n.voice:
            raise HTTPException(404, "Voice number not found")
        if data.destination == n.phone:
            raise HTTPException(422, "Cannot forward a number to itself")
        n.forwarding = data.destination
        audit(db, user, "forwarding.updated", n.id)
    return {"status": "updated"}


async def validate_twilio(request):
    params = dict(await request.form())
    with DB() as db:
        t = db.scalar(select(Tenant).where(Tenant.twilio_sid == params.get("AccountSid", "")))
        if not t:
            raise HTTPException(403, "Unknown account")
        token = decrypt(t.credentials)["auth_token"]
        url = settings.public_url + request.url.path
        if request.url.query:
            url += "?" + request.url.query
        if not RequestValidator(token).validate(url, params, request.headers.get("x-twilio-signature", "")):
            raise HTTPException(403, "Invalid signature")
        return t.id, params


@app.post("/webhooks/twilio/inbound")
async def inbound(request: Request):
    tenant_id, p = await validate_twilio(request)
    sid = p.get("MessageSid", "")
    if not sid:
        raise HTTPException(422, "Missing message identity")
    with DB.begin() as db:
        t = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        if db.get(Event, "inbound:" + sid):
            return Response("<Response/>", media_type="application/xml")
        raw_to = p.get("To", "")
        channel = p.get("ChannelPrefix", "") or (
            "whatsapp" if raw_to.startswith("whatsapp:") else "rcs" if raw_to.startswith("rcs:") else "sms"
        )
        if channel == "whatsapp":
            n = db.scalar(select(Number).where(Number.tenant_id == t.id, Number.whatsapp_sender == raw_to))
        elif channel == "rcs":
            n = db.scalar(
                select(Number).where(
                    Number.tenant_id == t.id, Number.rcs_service_sid == p.get("MessagingServiceSid", ""), Number.rcs_sender == raw_to
                )
            )
        else:
            n = db.scalar(select(Number).where(Number.tenant_id == t.id, Number.phone == raw_to))
        if not n or not channel_enabled(n, channel):
            raise HTTPException(404, "Number not found")
        body, peer = p.get("Body", "")[:1600], p.get("From", "").removeprefix("whatsapp:").removeprefix("rcs:")
        message = Message(
            tenant_id=t.id, number_id=n.id, peer=peer, body=body, channel=channel, direction="inbound", sid=sid, status="received"
        )
        db.add(message)
        db.flush()
        if body.strip().upper() in {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}:
            if not db.scalar(select(Suppression).where(Suppression.tenant_id == t.id, Suppression.peer == peer)):
                db.add(Suppression(tenant_id=t.id, peer=peer))
        elif body.strip().upper() in {"START", "UNSTOP"}:
            row = db.scalar(select(Suppression).where(Suppression.tenant_id == t.id, Suppression.peer == peer))
            if row:
                db.delete(row)
        inbound_ai(db, t, message)
        db.add(Event(id="inbound:" + sid, tenant_id=t.id, kind="sms.inbound"))
    return Response("<Response/>", media_type="application/xml")


@app.post("/webhooks/twilio/status")
async def message_status(request: Request):
    tenant_id, p = await validate_twilio(request)
    with DB.begin() as db:
        m = db.scalar(select(Message).where(Message.tenant_id == tenant_id, Message.sid == p.get("MessageSid")).with_for_update())
        if not m:
            # Worker may not yet have persisted the SID. Retry instead of dropping.
            raise HTTPException(503, "Message awaiting reconciliation")
        status = p.get("MessageStatus", "")
        ranks = {"queued": 0, "accepted": 0, "sending": 1, "sent": 2, "delivered": 3, "read": 4, "failed": 3, "undelivered": 3}
        if status in ranks and ranks[status] > ranks.get(m.status, -1) and m.status not in {"failed", "undelivered"}:
            m.status, m.error = status, p.get("ErrorCode", "")[:80]
    return Response(status_code=204)


@app.post("/webhooks/twilio/voice")
async def voice(request: Request):
    tenant_id, p = await validate_twilio(request)
    with DB.begin() as db:
        t = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        n = db.scalar(select(Number).where(Number.tenant_id == t.id, Number.phone == p.get("To")))
        sid = p.get("CallSid", "")
        profile = db.get(AIProfile, t.id)
        ai_allowed = profile and profile.voice_enabled and not profile.paused and ai_configured()
        allowed = n and n.voice and (n.forwarding or ai_allowed) and sid and t.billing_status == "active" and t.status == "approved"
        call = db.get(Call, sid) if sid else None
        if call and (not n or call.tenant_id != t.id or call.number_id != n.id):
            raise HTTPException(409, "Call ownership mismatch")
        minutes = call.reserved_minutes if call else 0
        if allowed and not call:
            month = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            calls = db.scalars(select(Call).where(Call.tenant_id == t.id, Call.created_at >= month)).all()
            committed = sum(c.billed_minutes if c.status == "completed" else c.reserved_minutes for c in calls)
            minutes = min(10, max(0, settings.voice_monthly_minutes - committed))
            allowed = minutes > 0 and within_budget(tenant_client(t), t)
            if allowed:
                call = Call(sid=sid, tenant_id=t.id, number_id=n.id, destination=n.forwarding, reserved_minutes=minutes)
                db.add(call)
        elif call and call.status == "completed":
            allowed = False
        if allowed and minutes:
            ai_xml = start_voice(db, t, n, call) if ai_allowed else None
            destination = call.destination
            callback = settings.public_url + "/webhooks/twilio/voice-status"
            xml = (
                '<Response><Dial timeout="20" timeLimit="'
                + str(minutes * 60)
                + '" action="'
                + escape(callback, {'"': "&quot;"})
                + '" method="POST"><Number>'
                + escape(destination)
                + "</Number></Dial></Response>"
            )
            if ai_xml:
                xml = ai_xml
        else:
            xml = "<Response><Say>This business is currently unavailable. Please try again later.</Say></Response>"
    return Response(xml, media_type="application/xml")


@app.post("/webhooks/twilio/voice-status")
async def voice_status(request: Request):
    tenant_id, p = await validate_twilio(request)
    with DB.begin() as db:
        call = db.scalar(select(Call).where(Call.sid == p.get("CallSid"), Call.tenant_id == tenant_id).with_for_update())
        if not call:
            raise HTTPException(404, "Call not found")
        if p.get("CallStatus") and p.get("CallStatus") != "completed":
            return Response("<Response/>", media_type="application/xml")
        try:
            seconds = int(p.get("CallDuration", p.get("DialCallDuration", "0")))
            if "CallDuration" not in p:
                seconds = max(seconds, int((now() - call.created_at.replace(tzinfo=now().tzinfo)).total_seconds()))
        except ValueError:
            raise HTTPException(422, "Invalid call duration")
        if seconds < 0 or seconds > call.reserved_minutes * 60 + 60:
            raise HTTPException(422, "Call duration outside reservation")
        if call.status != "completed" or "CallDuration" in p:
            call.billed_minutes, call.status = max(call.billed_minutes, (seconds + 59) // 60), "completed"
    return Response("<Response/>", media_type="application/xml")


@app.get("/api/usage")
def usage(user=Depends(current_user)):
    with DB() as db:
        month = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        sms = db.scalars(
            select(Message).where(Message.tenant_id == user.tenant_id, Message.direction == "outbound", Message.created_at >= month)
        ).all()
        calls = db.scalars(select(Call).where(Call.tenant_id == user.tenant_id, Call.created_at >= month)).all()
        return {
            "sms_segments": sum(message_units(m) for m in sms),
            "sms_allowance": settings.sms_monthly_segments,
            "voice_minutes": sum(c.billed_minutes if c.status == "completed" else c.reserved_minutes for c in calls),
            "voice_allowance": settings.voice_monthly_minutes,
        }


@app.get("/ready")
def ready(response: Response):
    with DB() as db:
        heartbeat = db.scalar(select(WorkerHeartbeat).order_by(WorkerHeartbeat.seen_at.desc()).limit(1))
        alive = heartbeat and heartbeat.seen_at.replace(tzinfo=now().tzinfo) > now() - timedelta(seconds=90)
    checks = {
        "database": True,
        "worker": bool(alive),
        "twilio": bool(settings.twilio_sid and settings.twilio_api_key and settings.twilio_api_secret),
        "stripe_live": settings.stripe_key.startswith(("rk_live_", "sk_live_")) and bool(settings.stripe_webhook_secret),
        "email": bool(settings.smtp_host and settings.email_from),
        "public_sales": settings.public_sales_enabled,
    }
    if not all(checks.values()):
        response.status_code = 503
    return {"ready": all(checks.values()), "checks": checks}


@app.get("/api/admin/tenants")
def admin_tenants(user=Depends(current_user)):
    if not user.platform_admin:
        raise HTTPException(403, "Platform administrator required")
    with DB() as db:
        return [
            {
                "id": t.id,
                "name": t.name,
                "legal_name": t.legal_name,
                "address": t.address,
                "registration_number": t.registration_number,
                "status": t.status,
                "connected": bool(t.twilio_sid),
                "billing_status": t.billing_status,
            }
            for t in db.scalars(select(Tenant)).all()
        ]


@app.post("/api/admin/tenants/{tenant_id}/connect", dependencies=[Depends(csrf)])
def connect(tenant_id: str, user=Depends(current_user)):
    if not user.platform_admin:
        raise HTTPException(403, "Platform administrator required")
    with DB.begin() as db:
        t = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        if not t:
            raise HTTPException(404, "Customer not found")
        if not t.twilio_sid:
            create_subaccount(t)
        db.add(Audit(tenant_id=t.id, actor=user.id, action="provider.connected"))
    return {"status": "connected"}


class Approval(BaseModel):
    bundle_sid: str = Field(pattern=r"^BU[0-9a-fA-F]{32}$")
    address_sid: str = Field(default="", pattern=r"^(AD[0-9a-fA-F]{32})?$")
    type: Literal["Local", "Mobile", "TollFree"]


@app.post("/api/admin/tenants/{tenant_id}/approve", dependencies=[Depends(csrf)])
def approve(tenant_id: str, data: Approval, user=Depends(current_user)):
    if not user.platform_admin:
        raise HTTPException(403, "Platform administrator required")
    with DB.begin() as db:
        t = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        if not t or not t.legal_name or not t.address:
            raise HTTPException(409, "Complete customer legal identity first")
        require_company_verified(db, t)
        if settings.company_verification_enabled or settings.environment == 'production':
            import json
            evidence = db.get(CompanyVerification, t.id)
            provider_state = json.loads(evidence.provider_state or '{}')
            if provider_state.get('stage') != 'approved' or provider_state.get('bundle') != data.bundle_sid:
                raise HTTPException(409, "Use the automatically verified customer bundle; unrelated bundle IDs cannot approve this company")
        client = tenant_client(t)
        bundle = client.numbers.v2.regulatory_compliance.bundles(data.bundle_sid).fetch()
        if bundle.status != "twilio-approved":
            raise HTTPException(409, "Twilio has not approved this bundle")
        regulation = client.numbers.v2.regulatory_compliance.regulations(bundle.regulation_sid).fetch()
        expected = {"Local": "local", "Mobile": "mobile", "TollFree": "toll-free"}[data.type]
        if regulation.iso_country != "GB" or regulation.number_type != expected or regulation.end_user_type != "business":
            raise HTTPException(409, "Bundle country, number type or end-user type mismatch")
        if data.address_sid:
            client.addresses(data.address_sid).fetch()
        t.status, t.bundle_sid, t.address_sid, t.bundle_type = "approved", data.bundle_sid, data.address_sid, data.type
        db.add(Audit(tenant_id=t.id, actor=user.id, action="compliance.approved", detail=data.bundle_sid))
    return {"status": "approved"}


@app.get("/api/service")
def service_info():
    return {
        "legal_name": settings.legal_business_name,
        "address": settings.legal_business_address,
        "support_email": settings.support_email,
        "billing_email": settings.billing_email,
        "terms_url": settings.terms_url,
        "privacy_url": settings.privacy_url,
        "terms_version": settings.terms_version,
        "registration_open": settings.registration_enabled,
        "sms_monthly_segments": settings.sms_monthly_segments,
        "voice_monthly_minutes": settings.voice_monthly_minutes,
    }


@app.post("/webhooks/twilio/ai-voice")
async def ai_voice(request: Request):
    tenant_id, params = await validate_twilio(request)
    from starlette.concurrency import run_in_threadpool

    xml = await run_in_threadpool(voice_turn, tenant_id, params, request.query_params.get("token", ""))
    return Response(xml, media_type="application/xml")


@app.post("/webhooks/twilio/relay-fallback")
async def relay_fallback(request: Request):
    tenant_id, params = await validate_twilio(request)
    from .ai import fallback_xml

    with DB.begin() as db:
        call = db.scalar(select(Call).where(Call.sid == params.get("CallSid"), Call.tenant_id == tenant_id).with_for_update())
        if not call:
            raise HTTPException(404, "Call not found")
        t = db.get(Tenant, tenant_id)
        remaining = max(0, call.reserved_minutes * 60 - int((now() - call.created_at.replace(tzinfo=now().tzinfo)).total_seconds()))
        if t.status != "approved" or t.billing_status != "active" or call.status == "completed":
            remaining = 0
        xml = fallback_xml(call, remaining)
    return Response(xml, media_type="application/xml")


@app.get("/payment-status")
def payment_status():
    return FileResponse("app/static/payment-status.html")
