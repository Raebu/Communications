"""Customer Communication OS: customer state, outcomes, guarantees and recovery."""
import hashlib
from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .models import (
    Audit,
    Customer,
    CustomerEvent,
    CustomerState,
    GuaranteeRule,
    Outcome,
    Promise,
    RecoveryJob,
    DB,
    now,
)
from .security import csrf, current_user, decrypt, encrypt

router = APIRouter(prefix="/api/customer-os")


def aware(value):
    if not value:
        return value
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def record_event(db, tenant_id, kind, channel, source_id="", customer_id=None, payload=None):
    event = CustomerEvent(
        tenant_id=tenant_id,
        customer_id=customer_id,
        kind=kind,
        channel=channel,
        source_id=source_id[:100],
        encrypted_payload=encrypt(payload or {}) if payload else "",
    )
    db.add(event)
    db.flush()
    evaluate_guarantees(db, tenant_id, kind, customer_id, event.id)
    return event


def evaluate_guarantees(db, tenant_id, event_kind, customer_id, event_id):
    rules = db.scalars(
        select(GuaranteeRule).where(
            GuaranteeRule.tenant_id == tenant_id,
            GuaranteeRule.event_kind == event_kind,
            GuaranteeRule.enabled.is_(True),
        )
    ).all()
    for rule in rules:
        due = now() + timedelta(minutes=rule.max_minutes)
        existing = db.scalar(
            select(RecoveryJob).where(
                RecoveryJob.tenant_id == tenant_id,
                RecoveryJob.kind == "guarantee",
                RecoveryJob.status.in_(["queued", "attention"]),
            )
        )
        if existing and existing.encrypted_payload:
            try:
                if decrypt(existing.encrypted_payload).get("event_id") == event_id:
                    continue
            except Exception:
                pass
        db.add(
            RecoveryJob(
                tenant_id=tenant_id,
                customer_id=customer_id,
                kind="guarantee",
                due_at=due,
                encrypted_payload=encrypt(
                    {
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "event_id": event_id,
                        "action": rule.action,
                    }
                ),
            )
        )


def require_customer(db, tenant_id, customer_id):
    customer = db.scalar(
        select(Customer).where(Customer.id == customer_id, Customer.tenant_id == tenant_id)
    )
    if not customer:
        raise HTTPException(404, "Customer not found")
    return customer


def customer_state(db, tenant_id, customer_id):
    customer = require_customer(db, tenant_id, customer_id)
    state = db.get(CustomerState, customer_id)
    if not state:
        state = CustomerState(customer_id=customer_id, tenant_id=tenant_id)
        db.add(state)
        db.flush()
    return customer, state


def state_payload(state):
    details = decrypt(state.encrypted_state) if state.encrypted_state else {}
    return {
        "customer_id": state.customer_id,
        "vip": state.vip,
        "owner": state.owner,
        "risk_score": state.risk_score,
        "revenue_signal": state.revenue_signal,
        "summary": details.get("summary", ""),
        "next_best_action": details.get("next_best_action", ""),
        "updated_at": state.updated_at.isoformat() if state.updated_at else None,
    }


class StateUpdate(BaseModel):
    vip: bool = False
    owner: str = Field(default="", max_length=100)
    risk_score: int = Field(default=0, ge=0, le=100)
    revenue_signal: int = Field(default=0, ge=0, le=100_000_000)
    summary: str = Field(default="", max_length=2000)
    next_best_action: str = Field(default="", max_length=500)


class OutcomeInput(BaseModel):
    customer_id: str | None = None
    conversation_id: str | None = None
    kind: str = Field(pattern=r"^(resolved|booked|quoted|escalated|payment_requested|callback|follow_up|complaint|sale_won|sale_lost|other)$")
    owner: str = Field(default="", max_length=100)
    due_at: str | None = None
    note: str = Field(default="", max_length=2000)


class PromiseInput(BaseModel):
    customer_id: str | None = None
    conversation_id: str | None = None
    owner: str = Field(default="", max_length=100)
    due_at: str
    commitment: str = Field(min_length=2, max_length=2000)


class GuaranteeInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    event_kind: str = Field(min_length=2, max_length=50, pattern=r"^[a-z0-9_.-]+$")
    max_minutes: int = Field(ge=1, le=10080)
    action: str = Field(default="alert", pattern=r"^(alert|callback_task|owner_escalation)$")
    enabled: bool = True


def parsed_due(value):
    if not value:
        return None
    try:
        parsed = __import__("datetime").datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, "Use a valid ISO date and time") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@router.get("/customers/{customer_id}/brain")
def brain(customer_id: str, user=Depends(current_user)):
    with DB.begin() as db:
        _, state = customer_state(db, user.tenant_id, customer_id)
        open_promises = db.scalar(
            select(func.count()).select_from(Promise).where(
                Promise.tenant_id == user.tenant_id,
                Promise.customer_id == customer_id,
                Promise.status == "open",
            )
        )
        open_outcomes = db.scalar(
            select(func.count()).select_from(Outcome).where(
                Outcome.tenant_id == user.tenant_id,
                Outcome.customer_id == customer_id,
                Outcome.status == "open",
            )
        )
        return {**state_payload(state), "open_promises": open_promises, "open_outcomes": open_outcomes}


@router.put("/customers/{customer_id}/brain", dependencies=[Depends(csrf)])
def brain_update(customer_id: str, data: StateUpdate, user=Depends(current_user)):
    if user.role != "owner":
        raise HTTPException(403, "Account owner required")
    with DB.begin() as db:
        _, state = customer_state(db, user.tenant_id, customer_id)
        state.vip = data.vip
        state.owner = data.owner.strip()
        state.risk_score = data.risk_score
        state.revenue_signal = data.revenue_signal
        state.encrypted_state = encrypt(
            {
                "summary": data.summary.strip(),
                "next_best_action": data.next_best_action.strip(),
            }
        )
        state.updated_at = now()
        db.add(Audit(tenant_id=user.tenant_id, actor=user.id, action="customer.brain.updated", detail=customer_id))
    return {"saved": True}


@router.get("/customers/{customer_id}/timeline")
def timeline(customer_id: str, user=Depends(current_user)):
    with DB() as db:
        require_customer(db, user.tenant_id, customer_id)
        events = db.scalars(
            select(CustomerEvent).where(
                CustomerEvent.tenant_id == user.tenant_id,
                CustomerEvent.customer_id == customer_id,
            ).order_by(CustomerEvent.occurred_at.desc()).limit(200)
        ).all()
        promises = db.scalars(
            select(Promise).where(
                Promise.tenant_id == user.tenant_id,
                Promise.customer_id == customer_id,
            ).order_by(Promise.created_at.desc()).limit(100)
        ).all()
        outcomes = db.scalars(
            select(Outcome).where(
                Outcome.tenant_id == user.tenant_id,
                Outcome.customer_id == customer_id,
            ).order_by(Outcome.created_at.desc()).limit(100)
        ).all()
        rows = []
        for event in events:
            rows.append({
                "type": "event",
                "id": event.id,
                "kind": event.kind,
                "channel": event.channel,
                "at": event.occurred_at.isoformat(),
                "detail": decrypt(event.encrypted_payload) if event.encrypted_payload else {},
            })
        for promise in promises:
            rows.append({
                "type": "promise",
                "id": promise.id,
                "kind": "promise",
                "status": promise.status,
                "owner": promise.owner,
                "at": promise.created_at.isoformat(),
                "due_at": promise.due_at.isoformat(),
                "detail": decrypt(promise.encrypted_commitment),
            })
        for outcome in outcomes:
            rows.append({
                "type": "outcome",
                "id": outcome.id,
                "kind": outcome.kind,
                "status": outcome.status,
                "owner": outcome.owner,
                "at": outcome.created_at.isoformat(),
                "due_at": outcome.due_at.isoformat() if outcome.due_at else None,
                "detail": decrypt(outcome.encrypted_payload) if outcome.encrypted_payload else {},
            })
        rows.sort(key=lambda item: item["at"], reverse=True)
        return rows[:300]


@router.post("/outcomes", dependencies=[Depends(csrf)])
def create_outcome(data: OutcomeInput, user=Depends(current_user)):
    with DB.begin() as db:
        if data.customer_id:
            customer_state(db, user.tenant_id, data.customer_id)
        outcome = Outcome(
            tenant_id=user.tenant_id,
            customer_id=data.customer_id,
            conversation_id=data.conversation_id,
            kind=data.kind,
            owner=data.owner.strip(),
            due_at=parsed_due(data.due_at),
            encrypted_payload=encrypt({"note": data.note.strip()}) if data.note.strip() else "",
        )
        db.add(outcome)
        db.flush()
        record_event(
            db, user.tenant_id, "outcome.created", "system", outcome.id,
            data.customer_id, {"kind": outcome.kind, "owner": outcome.owner},
        )
        return {"id": outcome.id, "status": outcome.status}


@router.post("/outcomes/{outcome_id}/complete", dependencies=[Depends(csrf)])
def complete_outcome(outcome_id: str, user=Depends(current_user)):
    with DB.begin() as db:
        outcome = db.scalar(
            select(Outcome).where(Outcome.id == outcome_id, Outcome.tenant_id == user.tenant_id).with_for_update()
        )
        if not outcome:
            raise HTTPException(404, "Outcome not found")
        outcome.status = "completed"
        outcome.completed_at = now()
        record_event(
            db, user.tenant_id, "outcome.completed", "system", outcome.id,
            outcome.customer_id, {"kind": outcome.kind},
        )
    return {"status": "completed"}


@router.post("/promises", dependencies=[Depends(csrf)])
def create_promise(data: PromiseInput, user=Depends(current_user)):
    due = parsed_due(data.due_at)
    if due <= now():
        raise HTTPException(422, "Promise due time must be in the future")
    with DB.begin() as db:
        if data.customer_id:
            customer_state(db, user.tenant_id, data.customer_id)
        promise = Promise(
            tenant_id=user.tenant_id,
            customer_id=data.customer_id,
            conversation_id=data.conversation_id,
            owner=data.owner.strip(),
            due_at=due,
            encrypted_commitment=encrypt({"commitment": data.commitment.strip()}),
        )
        db.add(promise)
        db.flush()
        record_event(
            db, user.tenant_id, "promise.created", "system", promise.id,
            data.customer_id, {"owner": promise.owner, "due_at": due.isoformat()},
        )
        return {"id": promise.id, "status": promise.status}


@router.post("/promises/{promise_id}/complete", dependencies=[Depends(csrf)])
def complete_promise(promise_id: str, user=Depends(current_user)):
    with DB.begin() as db:
        promise = db.scalar(
            select(Promise).where(Promise.id == promise_id, Promise.tenant_id == user.tenant_id).with_for_update()
        )
        if not promise:
            raise HTTPException(404, "Promise not found")
        promise.status = "completed"
        promise.completed_at = now()
        record_event(
            db, user.tenant_id, "promise.completed", "system", promise.id,
            promise.customer_id, {},
        )
    return {"status": "completed"}


@router.get("/guarantees")
def guarantees(user=Depends(current_user)):
    with DB() as db:
        return [
            {
                "id": rule.id,
                "name": rule.name,
                "event_kind": rule.event_kind,
                "max_minutes": rule.max_minutes,
                "action": rule.action,
                "enabled": rule.enabled,
            }
            for rule in db.scalars(
                select(GuaranteeRule).where(GuaranteeRule.tenant_id == user.tenant_id).order_by(GuaranteeRule.created_at)
            ).all()
        ]


@router.post("/guarantees", dependencies=[Depends(csrf)])
def create_guarantee(data: GuaranteeInput, user=Depends(current_user)):
    if user.role != "owner":
        raise HTTPException(403, "Account owner required")
    with DB.begin() as db:
        rule = GuaranteeRule(tenant_id=user.tenant_id, **data.model_dump())
        db.add(rule)
        db.flush()
        db.add(Audit(tenant_id=user.tenant_id, actor=user.id, action="guarantee.created", detail=rule.id))
        return {"id": rule.id}


@router.put("/guarantees/{rule_id}", dependencies=[Depends(csrf)])
def update_guarantee(rule_id: str, data: GuaranteeInput, user=Depends(current_user)):
    if user.role != "owner":
        raise HTTPException(403, "Account owner required")
    with DB.begin() as db:
        rule = db.scalar(
            select(GuaranteeRule).where(GuaranteeRule.id == rule_id, GuaranteeRule.tenant_id == user.tenant_id).with_for_update()
        )
        if not rule:
            raise HTTPException(404, "Guarantee not found")
        for key, value in data.model_dump().items():
            setattr(rule, key, value)
        db.add(Audit(tenant_id=user.tenant_id, actor=user.id, action="guarantee.updated", detail=rule.id))
    return {"saved": True}


@router.get("/recovery")
def recovery(user=Depends(current_user)):
    with DB() as db:
        jobs = db.scalars(
            select(RecoveryJob).where(
                RecoveryJob.tenant_id == user.tenant_id,
                RecoveryJob.status.in_(["queued", "attention"]),
            ).order_by(RecoveryJob.due_at).limit(200)
        ).all()
        return [
            {
                "id": job.id,
                "kind": job.kind,
                "status": job.status,
                "due_at": job.due_at.isoformat(),
                "attempts": job.attempts,
                "detail": decrypt(job.encrypted_payload) if job.encrypted_payload else {},
            }
            for job in jobs
        ]


@router.post("/recovery/{job_id}/resolve", dependencies=[Depends(csrf)])
def resolve_recovery(job_id: str, user=Depends(current_user)):
    with DB.begin() as db:
        job = db.scalar(
            select(RecoveryJob).where(RecoveryJob.id == job_id, RecoveryJob.tenant_id == user.tenant_id).with_for_update()
        )
        if not job:
            raise HTTPException(404, "Recovery item not found")
        job.status = "resolved"
        record_event(db, user.tenant_id, "recovery.resolved", "system", job.id, job.customer_id, {"kind": job.kind})
    return {"status": "resolved"}


@router.get("/executive-brief")
def executive_brief(user=Depends(current_user)):
    with DB() as db:
        since = now() - timedelta(hours=24)
        interactions = db.scalar(
            select(func.count()).select_from(CustomerEvent).where(
                CustomerEvent.tenant_id == user.tenant_id,
                CustomerEvent.occurred_at >= since,
            )
        )
        open_promises = db.scalar(
            select(func.count()).select_from(Promise).where(
                Promise.tenant_id == user.tenant_id,
                Promise.status == "open",
            )
        )
        overdue_promises = db.scalar(
            select(func.count()).select_from(Promise).where(
                Promise.tenant_id == user.tenant_id,
                Promise.status == "open",
                Promise.due_at < now(),
            )
        )
        recoveries = db.scalar(
            select(func.count()).select_from(RecoveryJob).where(
                RecoveryJob.tenant_id == user.tenant_id,
                RecoveryJob.status.in_(["queued", "attention"]),
            )
        )
        at_risk = db.scalar(
            select(func.count()).select_from(CustomerState).where(
                CustomerState.tenant_id == user.tenant_id,
                CustomerState.risk_score >= 70,
            )
        )
        potential_revenue = db.scalar(
            select(func.coalesce(func.sum(CustomerState.revenue_signal), 0)).where(
                CustomerState.tenant_id == user.tenant_id
            )
        )
        open_outcomes = db.scalar(
            select(func.count()).select_from(Outcome).where(
                Outcome.tenant_id == user.tenant_id,
                Outcome.status == "open",
            )
        )
        text = (
            f"Last 24 hours: {interactions} customer interactions. "
            f"{open_promises} open promises ({overdue_promises} overdue), "
            f"{recoveries} recovery items, {open_outcomes} open outcomes and "
            f"{at_risk} customers currently marked at risk."
        )
        return {
            "interactions_24h": interactions,
            "open_promises": open_promises,
            "overdue_promises": overdue_promises,
            "open_recoveries": recoveries,
            "open_outcomes": open_outcomes,
            "customers_at_risk": at_risk,
            "potential_revenue": int(potential_revenue or 0),
            "summary": text,
        }


def obligation_one():
    worked = False
    with DB.begin() as db:
        promise = db.scalar(
            select(Promise).where(Promise.status == "open", Promise.due_at <= now())
            .order_by(Promise.due_at).with_for_update(skip_locked=True).limit(1)
        )
        if promise:
            promise.status = "overdue"
            db.add(
                RecoveryJob(
                    tenant_id=promise.tenant_id,
                    customer_id=promise.customer_id,
                    kind="promise_overdue",
                    status="attention",
                    due_at=now(),
                    encrypted_payload=encrypt({"promise_id": promise.id, "owner": promise.owner}),
                )
            )
            if promise.customer_id:
                state = db.get(CustomerState, promise.customer_id)
                if state:
                    state.risk_score = min(100, state.risk_score + 10)
            db.add(Audit(tenant_id=promise.tenant_id, actor="system", action="promise.overdue", detail=promise.id))
            worked = True

        job = db.scalar(
            select(RecoveryJob).where(RecoveryJob.status == "queued", RecoveryJob.due_at <= now())
            .order_by(RecoveryJob.due_at).with_for_update(skip_locked=True).limit(1)
        )
        if job:
            job.status = "attention"
            job.attempts += 1
            db.add(Audit(tenant_id=job.tenant_id, actor="system", action="recovery.needs_attention", detail=job.id))
            worked = True
    return worked


def identity_digest(tenant_id, kind, value):
    return hashlib.sha256((tenant_id + ":" + kind + ":" + value.strip().lower()).encode()).hexdigest()
