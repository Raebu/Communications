"""Operator controls, content lifecycle and scoped customer integration API."""

import hashlib
import json
import secrets
from datetime import timedelta, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, exists
from .config import settings
from .models import (
    AIJob,
    ActionJob,
    Audit,
    Booking,
    CustomerAPIKey,
    DB,
    FinancialEntry,
    IdentityChallenge,
    KnowledgeRevision,
    Lead,
    Message,
    Tenant,
    VoiceSession,
    now,
)
from .security import csrf, current_user, encrypt, rate_limit
from .autonomy import owner

router = APIRouter()


class ReviewInput(BaseModel):
    outcome: Literal["verified_completed", "verified_not_executed"]
    reference: str = Field(min_length=8, max_length=300)


@router.post("/api/actions/{action_id}/review", dependencies=[Depends(csrf)])
def review(action_id: str, data: ReviewInput, user=Depends(current_user)):
    owner(user)
    with DB.begin() as db:
        db.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
        j = db.scalar(select(ActionJob).where(ActionJob.id == action_id, ActionJob.tenant_id == user.tenant_id).with_for_update())
        if not j:
            raise HTTPException(404, "Action not found")
        if j.status != "review":
            raise HTTPException(409, "Only unresolved review actions can be reconciled")
        # A human attestation never re-executes an external action or sends a success notification.
        j.status = "reconciled" if data.outcome == "verified_completed" else "cancelled"
        j.receipt = json.dumps({"operator_outcome": data.outcome, "evidence_reference": data.reference, "actor": user.id})
        db.add(Audit(tenant_id=user.tenant_id, actor=user.id, action="action.reconciled", detail=j.id))
    return {"status": j.status}


@router.post("/api/actions/{action_id}/cancel", dependencies=[Depends(csrf)])
def cancel_action(action_id: str, user=Depends(current_user)):
    owner(user)
    with DB.begin() as db:
        db.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
        j = db.scalar(select(ActionJob).where(ActionJob.id == action_id, ActionJob.tenant_id == user.tenant_id).with_for_update())
        if not j:
            raise HTTPException(404, "Action not found")
        if j.status not in {"queued", "awaiting_confirmation"}:
            raise HTTPException(409, "Action already processed or requires reconciliation")
        j.status = "cancelled"
        db.add(Audit(tenant_id=user.tenant_id, actor=user.id, action="action.cancelled", detail=j.id))
    return {"cancelled": True}


@router.get("/api/knowledge/{knowledge_id}/versions")
def versions(knowledge_id: str, user=Depends(current_user)):
    owner(user)
    with DB() as db:
        return [
            {"version": r.version, "snapshot": json.loads(r.snapshot), "created_at": r.created_at}
            for r in db.scalars(
                select(KnowledgeRevision)
                .where(KnowledgeRevision.tenant_id == user.tenant_id, KnowledgeRevision.knowledge_id == knowledge_id)
                .order_by(KnowledgeRevision.version)
            )
        ]


def snapshot(db, k):
    if not db.scalar(select(KnowledgeRevision).where(KnowledgeRevision.knowledge_id == k.id, KnowledgeRevision.version == k.version)):
        db.add(
            KnowledgeRevision(
                tenant_id=k.tenant_id,
                knowledge_id=k.id,
                version=k.version,
                snapshot=json.dumps(
                    {
                        "title": k.title,
                        "content": k.content,
                        "source": k.source,
                        "approved": k.approved,
                        "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                    }
                ),
            )
        )


class FinanceInput(BaseModel):
    kind: Literal["revenue", "cost"]
    category: Literal["service", "ai", "messaging", "voice", "hosting", "other"]
    amount: int = Field(gt=0, le=100000000)
    period: str = Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    reference: str = Field(min_length=8, max_length=100)


@router.post("/api/finance", dependencies=[Depends(csrf)])
def finance(data: FinanceInput, user=Depends(current_user)):
    owner(user)
    with DB.begin() as db:
        db.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
        previous = db.scalar(
            select(FinancialEntry).where(FinancialEntry.tenant_id == user.tenant_id, FinancialEntry.reference == data.reference)
        )
        if previous:
            if any(getattr(previous, k) != v for k, v in data.model_dump().items()):
                raise HTTPException(409, "Evidence reference already has another entry")
            return {"id": previous.id}
        entry = FinancialEntry(tenant_id=user.tenant_id, **data.model_dump())
        db.add(entry)
        db.flush()
        db.add(Audit(tenant_id=user.tenant_id, actor=user.id, action="finance.recorded", detail=entry.id))
        return {"id": entry.id}


@router.get("/api/finance")
def finance_summary(period: str = Query(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$"), user=Depends(current_user)):
    owner(user)
    with DB() as db:
        entries = db.scalars(
            select(FinancialEntry).where(FinancialEntry.tenant_id == user.tenant_id, FinancialEntry.period == period)
        ).all()
        revenue = sum(e.amount for e in entries if e.kind == "revenue")
        costs = sum(e.amount for e in entries if e.kind == "cost")
        return {
            "currency": "gbp",
            "period": period,
            "recorded_revenue": revenue,
            "recorded_costs": costs,
            "recorded_margin": revenue - costs,
            "complete_ledger_verified": False,
            "note": "Amounts are pence. This reflects recorded evidence only, not verified total profit.",
        }


class KeyInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[Literal["leads:read", "bookings:read", "events:read"]] = Field(min_length=1, max_length=3)


@router.post("/api/integration-keys", dependencies=[Depends(csrf)])
def create_key(data: KeyInput, user=Depends(current_user)):
    owner(user)
    rate_limit("api-key:" + user.tenant_id, 5)
    token = "rc_" + secrets.token_urlsafe(32)
    with DB.begin() as db:
        key = CustomerAPIKey(
            tenant_id=user.tenant_id,
            name=data.name,
            scopes=",".join(sorted(set(data.scopes))),
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=now() + timedelta(days=90),
        )
        db.add(key)
        db.flush()
        db.add(Audit(tenant_id=user.tenant_id, actor=user.id, action="api.key.created", detail=key.id))
        return {"id": key.id, "token": token, "expires_at": key.expires_at}


@router.get("/api/integration-keys")
def list_keys(user=Depends(current_user)):
    owner(user)
    with DB() as db:
        return [
            {"id": k.id, "name": k.name, "scopes": k.scopes.split(","), "revoked": k.revoked, "expires_at": k.expires_at}
            for k in db.scalars(select(CustomerAPIKey).where(CustomerAPIKey.tenant_id == user.tenant_id))
        ]


@router.delete("/api/integration-keys/{key_id}", dependencies=[Depends(csrf)])
def revoke_key(key_id: str, user=Depends(current_user)):
    owner(user)
    with DB.begin() as db:
        key = db.scalar(
            select(CustomerAPIKey).where(CustomerAPIKey.id == key_id, CustomerAPIKey.tenant_id == user.tenant_id).with_for_update()
        )
        if not key:
            raise HTTPException(404, "Key not found")
        key.revoked = True
        db.add(Audit(tenant_id=user.tenant_id, actor=user.id, action="api.key.revoked", detail=key.id))
    return {"revoked": True}


def scope(required):
    def authenticate(request: Request):
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer rc_") or len(auth) > 200:
            raise HTTPException(401, "Integration key required")
        token = auth[7:]
        with DB() as db:
            key = db.get(CustomerAPIKey, hashlib.sha256(token.encode()).hexdigest())
            if not key or key.revoked or key.expires_at.replace(tzinfo=timezone.utc) <= now():
                raise HTTPException(401, "Integration key unavailable")
            if required not in key.scopes.split(","):
                raise HTTPException(403, "Key does not permit this operation")
            t = db.get(Tenant, key.tenant_id)
            if t.status != "approved" or t.billing_status != "active":
                raise HTTPException(403, "Account unavailable")
            identity = key.tenant_id
        rate_limit("integration:" + identity, 60)
        return identity

    return authenticate


@router.get("/api/customer/v1/leads")
def customer_leads(tenant_id=Depends(scope("leads:read"))):
    with DB() as db:
        return [
            {"id": lead.id, "summary": lead.summary, "status": lead.status}
            for lead in db.scalars(select(Lead).where(Lead.tenant_id == tenant_id).limit(200))
        ]


@router.get("/api/customer/v1/bookings")
def customer_bookings(tenant_id=Depends(scope("bookings:read"))):
    with DB() as db:
        return [
            {"id": b.id, "starts_at": b.starts_at, "ends_at": b.ends_at, "status": b.status}
            for b in db.scalars(select(Booking).where(Booking.tenant_id == tenant_id).order_by(Booking.created_at.desc()).limit(200))
        ]


@router.get("/api/customer/v1/events")
def customer_events(after: str = "", limit: int = Query(default=100, ge=1, le=200), tenant_id=Depends(scope("events:read"))):
    with DB() as db:
        filters = [Audit.tenant_id == tenant_id]
        if after:
            cursor = db.get(Audit, after)
            if not cursor or cursor.tenant_id != tenant_id:
                raise HTTPException(404, "Cursor not found")
            filters.append((Audit.created_at > cursor.created_at) | ((Audit.created_at == cursor.created_at) & (Audit.id > cursor.id)))
        rows = db.scalars(select(Audit).where(*filters).order_by(Audit.created_at, Audit.id).limit(limit)).all()
        return {
            "events": [{"id": a.id, "type": a.action, "created_at": a.created_at} for a in rows],
            "next_cursor": rows[-1].id if rows else after,
        }


def retention_one():
    """Daily content minimisation, preserving billing/consent/reconciliation metadata."""
    with DB.begin() as db:
        recent = exists(
            select(Audit.id).where(
                Audit.tenant_id == Tenant.id, Audit.action == "privacy.retention", Audit.created_at > now() - timedelta(days=1)
            )
        )
        tenants = db.scalars(select(Tenant).where(~recent).limit(10).with_for_update(skip_locked=True)).all()
        if not tenants:
            return False
        for t in tenants:
            for m in db.scalars(
                select(Message).where(
                    Message.tenant_id == t.id, Message.created_at < now() - timedelta(days=settings.message_retention_days)
                )
            ):
                m.body, m.sensitive_payload = "[Content removed by retention policy]", ""
            old_messages = select(Message.id).where(
                Message.tenant_id == t.id, Message.created_at < now() - timedelta(days=settings.message_retention_days)
            )
            for draft in db.scalars(select(AIJob).where(AIJob.tenant_id == t.id, AIJob.message_id.in_(old_messages))):
                draft.reply = ""
            for m in db.scalars(
                select(Message).where(
                    Message.tenant_id == t.id, Message.sensitive_payload != "", Message.created_at < now() - timedelta(minutes=10)
                )
            ):
                m.sensitive_payload = ""
                if m.status == "queued":
                    m.status, m.error = "failed", "verification_expired"
            for v in db.scalars(select(VoiceSession).where(VoiceSession.tenant_id == t.id)):
                from .models import Call

                call = db.get(Call, v.sid)
                if call.created_at.replace(tzinfo=timezone.utc) < now() - timedelta(days=settings.voice_retention_days):
                    v.history, v.last_xml = "", ""
            for proof in db.scalars(
                select(IdentityChallenge).where(IdentityChallenge.tenant_id == t.id, IdentityChallenge.expires_at < now())
            ):
                db.delete(proof)
            for j in db.scalars(select(ActionJob).where(ActionJob.tenant_id == t.id)):
                age = now() - j.created_at.replace(tzinfo=timezone.utc)
                if j.status == "awaiting_confirmation" and age > timedelta(minutes=15):
                    j.status = "cancelled"
                if j.status in {"completed", "cancelled", "reconciled"} and age > timedelta(days=settings.message_retention_days):
                    j.payload = encrypt({})
            for lead in db.scalars(
                select(Lead).where(Lead.tenant_id == t.id, Lead.created_at < now() - timedelta(days=settings.message_retention_days))
            ):
                lead.summary = "[Content removed by retention policy]"
            db.add(Audit(tenant_id=t.id, actor="worker", action="privacy.retention"))
    return True
