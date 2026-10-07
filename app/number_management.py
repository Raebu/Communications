"""Number porting intake and controlled owner-initiated click-to-call."""
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse

from .config import settings
from .models import Audit, DB, Number, OutboundCallRequest, PortRequest, Suppression, Tenant, now
from .providers import tenant_client
from .security import csrf, current_user, decrypt, encrypt, rate_limit

router = APIRouter()
PHONE = r"^\+44(?:[12]\d{9}|7[1-57-9]\d{8})$"


def _owner(user):
    if user.role != "owner":
        raise HTTPException(403, "Account owner required")


class PortRequestInput(BaseModel):
    phone: str = Field(pattern=PHONE)
    current_carrier: str = Field(min_length=2, max_length=120)
    account_number: str = Field(min_length=2, max_length=120)
    authorized_name: str = Field(min_length=2, max_length=160)
    service_address: str = Field(min_length=10, max_length=1000)
    recent_bill_ready: bool
    authority_confirmed: bool


class PortStatusInput(BaseModel):
    status: str = Field(pattern=r"^(review|needs_documents|submitted|carrier_review|scheduled|completed|rejected|cancelled)$")
    provider_reference: str = Field(default="", max_length=80)
    note: str = Field(default="", max_length=500)


class OutboundCallInput(BaseModel):
    number_id: str
    destination: str = Field(pattern=PHONE)
    request_key: str = Field(min_length=16, max_length=100)
    consent_confirmed: bool


def _port_view(row):
    detail = decrypt(row.encrypted_payload) if row.encrypted_payload else {}
    return {
        "id": row.id,
        "phone": row.phone,
        "status": row.status,
        "provider_reference": row.provider_reference,
        "current_carrier": detail.get("current_carrier", ""),
        "authorized_name": detail.get("authorized_name", ""),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "note": detail.get("note", ""),
    }


@router.get("/api/number-porting")
def list_ports(user=Depends(current_user)):
    _owner(user)
    with DB() as db:
        rows = db.scalars(
            select(PortRequest)
            .where(PortRequest.tenant_id == user.tenant_id)
            .order_by(PortRequest.created_at.desc())
        ).all()
        return [_port_view(row) for row in rows]


@router.post("/api/number-porting", dependencies=[Depends(csrf)])
def create_port(data: PortRequestInput, user=Depends(current_user)):
    _owner(user)
    if not data.authority_confirmed:
        raise HTTPException(422, "Confirm that you are authorised to port this number")
    if not data.recent_bill_ready:
        raise HTTPException(422, "A recent carrier bill is required before a port can be submitted")
    with DB.begin() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
        if tenant.status != "approved":
            raise HTTPException(409, "Complete company verification before requesting a number port")
        if db.scalar(select(Number).where(Number.phone == data.phone)):
            raise HTTPException(409, "This number is already active in Raeburn")
        existing = db.scalar(
            select(PortRequest).where(
                PortRequest.tenant_id == user.tenant_id,
                PortRequest.phone == data.phone,
            )
        )
        if existing:
            return _port_view(existing)
        row = PortRequest(
            tenant_id=user.tenant_id,
            phone=data.phone,
            status="review",
            encrypted_payload=encrypt({
                "current_carrier": data.current_carrier.strip(),
                "account_number": data.account_number.strip(),
                "authorized_name": data.authorized_name.strip(),
                "service_address": data.service_address.strip(),
                "recent_bill_ready": True,
                "authority_confirmed": True,
            }),
        )
        db.add(row)
        db.flush()
        db.add(Audit(
            tenant_id=user.tenant_id,
            actor=user.id,
            action="number_port.requested",
            detail=row.id,
        ))
        return _port_view(row)


@router.get("/api/admin/number-porting")
def admin_ports(user=Depends(current_user)):
    if not user.platform_admin:
        raise HTTPException(403, "Platform administrator required")
    with DB() as db:
        rows = db.scalars(select(PortRequest).order_by(PortRequest.created_at)).all()
        return [_port_view(row) | {"tenant_id": row.tenant_id} for row in rows]


@router.put("/api/admin/number-porting/{request_id}", dependencies=[Depends(csrf)])
def update_port(request_id: str, data: PortStatusInput, user=Depends(current_user)):
    if not user.platform_admin:
        raise HTTPException(403, "Platform administrator required")
    with DB.begin() as db:
        row = db.scalar(select(PortRequest).where(PortRequest.id == request_id).with_for_update())
        if not row:
            raise HTTPException(404, "Port request not found")
        detail = decrypt(row.encrypted_payload) if row.encrypted_payload else {}
        if data.note:
            detail["note"] = data.note.strip()
        row.encrypted_payload = encrypt(detail)
        row.status = data.status
        row.provider_reference = data.provider_reference.strip()
        row.updated_at = now()
        db.add(Audit(
            tenant_id=row.tenant_id,
            actor=user.id,
            action="number_port.status_changed",
            detail=row.id + ":" + row.status,
        ))
        return _port_view(row)


def _within_budget(client, tenant):
    rows = client.usage.records.this_month.list(category="totalprice", limit=1)
    if not rows:
        raise RuntimeError("Usage totals unavailable")
    if rows[0].price_unit.lower() != "usd":
        raise RuntimeError("Unsupported provider billing currency")
    return abs(Decimal(str(rows[0].price))) * 100 < tenant.spend_limit


def _call_view(row):
    return {
        "id": row.id,
        "number_id": row.number_id,
        "destination": row.destination,
        "status": row.status,
        "error": row.error,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get("/api/outbound-calls")
def list_outbound_calls(user=Depends(current_user)):
    _owner(user)
    with DB() as db:
        rows = db.scalars(
            select(OutboundCallRequest)
            .where(OutboundCallRequest.tenant_id == user.tenant_id)
            .order_by(OutboundCallRequest.created_at.desc())
            .limit(100)
        ).all()
        return [_call_view(row) for row in rows]


@router.post("/api/outbound-calls", dependencies=[Depends(csrf)])
def start_outbound_call(data: OutboundCallInput, user=Depends(current_user)):
    _owner(user)
    if not data.consent_confirmed:
        raise HTTPException(422, "Confirm that you have permission to call this person")
    rate_limit("outbound-call:" + user.tenant_id, 5)
    with DB.begin() as db:
        tenant = db.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
        number = db.scalar(
            select(Number).where(
                Number.id == data.number_id,
                Number.tenant_id == user.tenant_id,
                Number.voice.is_(True),
            )
        )
        if not number:
            raise HTTPException(404, "Voice number not found")
        if tenant.status != "approved" or tenant.billing_status != "active":
            raise HTTPException(409, "An active approved account is required")
        if not number.forwarding:
            raise HTTPException(409, "Set a forwarding phone first; Raeburn calls you before connecting the customer")
        if data.destination == number.forwarding or data.destination == number.phone:
            raise HTTPException(422, "Choose a separate customer destination")
        if db.scalar(
            select(Suppression).where(
                Suppression.tenant_id == user.tenant_id,
                Suppression.peer == data.destination,
            )
        ):
            raise HTTPException(409, "This person has opted out of contact")
        existing = db.scalar(
            select(OutboundCallRequest).where(
                OutboundCallRequest.tenant_id == user.tenant_id,
                OutboundCallRequest.request_key == data.request_key,
            )
        )
        if existing:
            return _call_view(existing)
        since = now() - timedelta(days=1)
        count = db.scalar(
            select(func.count()).select_from(OutboundCallRequest).where(
                OutboundCallRequest.tenant_id == user.tenant_id,
                OutboundCallRequest.created_at >= since,
            )
        ) or 0
        if count >= 20:
            raise HTTPException(429, "Daily click-to-call limit reached")
        row = OutboundCallRequest(
            tenant_id=user.tenant_id,
            number_id=number.id,
            request_key=data.request_key,
            destination=data.destination,
            agent_destination=number.forwarding,
            status="starting",
        )
        db.add(row)
        db.flush()
        request_id = row.id
        client = tenant_client(tenant)
        if not _within_budget(client, tenant):
            row.status = "blocked"
            row.error = "spend_limit"
            return _call_view(row)
        try:
            remote = client.calls.create(
                to=number.forwarding,
                from_=number.phone,
                url=settings.public_url + "/webhooks/twilio/outbound-connect?request=" + request_id,
                method="POST",
                status_callback=settings.public_url + "/webhooks/twilio/outbound-status?request=" + request_id,
                status_callback_method="POST",
                status_callback_event=["initiated", "ringing", "answered", "completed"],
                timeout=30,
            )
        except Exception:
            row.status = "review"
            row.error = "provider_result_unknown"
            db.add(Audit(
                tenant_id=user.tenant_id,
                actor=user.id,
                action="outbound_call.review_required",
                detail=request_id,
            ))
            return _call_view(row)
        row.provider_sid = remote.sid
        row.status = "calling_agent"
        row.updated_at = now()
        db.add(Audit(
            tenant_id=user.tenant_id,
            actor=user.id,
            action="outbound_call.started",
            detail=request_id,
        ))
        return _call_view(row)


async def _validated_provider_request(request: Request):
    request_id = request.query_params.get("request", "")
    if not request_id:
        raise HTTPException(403, "Missing request reference")
    with DB() as db:
        row = db.get(OutboundCallRequest, request_id)
        tenant = db.get(Tenant, row.tenant_id) if row else None
        if not row or not tenant or not tenant.credentials:
            raise HTTPException(403, "Unknown call request")
        token = decrypt(tenant.credentials).get("auth_token", "")
    form = dict(await request.form())
    url = settings.public_url + request.url.path + "?request=" + request_id
    if not token or not RequestValidator(token).validate(url, form, request.headers.get("x-twilio-signature", "")):
        raise HTTPException(403, "Invalid provider signature")
    return request_id, form


@router.post("/webhooks/twilio/outbound-connect")
async def outbound_connect(request: Request):
    request_id, form = await _validated_provider_request(request)
    with DB.begin() as db:
        row = db.scalar(select(OutboundCallRequest).where(OutboundCallRequest.id == request_id).with_for_update())
        number = db.get(Number, row.number_id)
        if row.status in {"completed", "failed", "cancelled"}:
            response = VoiceResponse()
            response.hangup()
            return Response(str(response), media_type="application/xml")
        row.status = "connecting_customer"
        row.updated_at = now()
        response = VoiceResponse()
        response.say("Raeburn outbound call. Connecting you now.", language="en-GB")
        dial = response.dial(
            caller_id=number.phone,
            timeout=30,
            time_limit=1800,
            action=settings.public_url + "/webhooks/twilio/outbound-result?request=" + row.id,
            method="POST",
        )
        dial.number(row.destination)
        return Response(str(response), media_type="application/xml")


@router.post("/webhooks/twilio/outbound-result")
async def outbound_result(request: Request):
    request_id, form = await _validated_provider_request(request)
    dial_status = str(form.get("DialCallStatus", "")).lower()
    with DB.begin() as db:
        row = db.scalar(select(OutboundCallRequest).where(OutboundCallRequest.id == request_id).with_for_update())
        if dial_status == "completed":
            row.status = "completed"
            row.error = ""
        elif dial_status in {"busy", "no-answer", "failed", "canceled"}:
            row.status = "failed"
            row.error = dial_status[:80]
        else:
            row.status = "review"
            row.error = "unexpected_dial_status"
        row.updated_at = now()
    response = VoiceResponse()
    response.hangup()
    return Response(str(response), media_type="application/xml")


@router.post("/webhooks/twilio/outbound-status", status_code=204)
async def outbound_status(request: Request):
    request_id, form = await _validated_provider_request(request)
    call_status = str(form.get("CallStatus", "")).lower()
    with DB.begin() as db:
        row = db.scalar(select(OutboundCallRequest).where(OutboundCallRequest.id == request_id).with_for_update())
        if row.status not in {"completed", "failed", "review"}:
            mapping = {
                "queued": "calling_agent",
                "initiated": "calling_agent",
                "ringing": "calling_agent",
                "in-progress": "agent_answered",
                "completed": "agent_leg_completed",
                "busy": "failed",
                "no-answer": "failed",
                "failed": "failed",
                "canceled": "cancelled",
            }
            if call_status in mapping:
                row.status = mapping[call_status]
                if row.status in {"failed", "cancelled"}:
                    row.error = call_status[:80]
                row.updated_at = now()
    return Response(status_code=204)
