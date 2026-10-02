"""Opt-in daily quality checks; known regressions pause automation."""

from datetime import timedelta
from time import monotonic
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from .models import AIProfile, Audit, DB, EvaluationSchedule, Tenant, now
from .autonomy import EvaluationSuite, knowledge_profile, owner
from .security import csrf, current_user, decrypt, encrypt

router = APIRouter()


class ScheduleInput(EvaluationSuite):
    enabled: bool = False


@router.put("/api/ai/quality-schedule", dependencies=[Depends(csrf)])
def save(data: ScheduleInput, user=Depends(current_user)):
    owner(user)
    with DB.begin() as db:
        db.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
        schedule = db.get(EvaluationSchedule, user.tenant_id)
        if schedule and schedule.status == "running":
            raise HTTPException(409, "Quality run is in progress")
        if not schedule:
            schedule = EvaluationSchedule(tenant_id=user.tenant_id)
            db.add(schedule)
        schedule.cases = encrypt({"cases": [c.model_dump() for c in data.cases]})
        schedule.enabled, schedule.status, schedule.next_run_at = data.enabled, "idle", now()
        schedule.failures = 0
        db.add(Audit(tenant_id=user.tenant_id, actor=user.id, action="quality.schedule.updated"))
    return {"saved": True}


@router.get("/api/ai/quality-schedule")
def get(user=Depends(current_user)):
    owner(user)
    with DB() as db:
        schedule = db.get(EvaluationSchedule, user.tenant_id)
        if not schedule:
            return {"enabled": False, "status": "unconfigured"}
        return {
            "enabled": schedule.enabled,
            "status": schedule.status,
            "next_run_at": schedule.next_run_at,
            "consecutive_failures": schedule.failures,
            "last_result": decrypt(schedule.last_result) if schedule.last_result else None,
        }


def evaluate_cases(cases, profiles, generate):
    results = []
    for case, profile in zip(cases, profiles):
        started = monotonic()
        try:
            answer = generate(profile, [{"role": "user", "content": case["question"]}])
            missing = [term for term in case.get("expected_terms", []) if term.lower() not in answer.lower()]
            forbidden = [term for term in case.get("forbidden_terms", []) if term.lower() in answer.lower()]
            results.append(
                {
                    "reply": answer,
                    "passed": not missing and not forbidden,
                    "missing": missing,
                    "forbidden": forbidden,
                    "latency_ms": round((monotonic() - started) * 1000),
                }
            )
        except Exception:
            results.append({"passed": False, "error": "model_unavailable"})
    return {"results": results, "passed": all(r["passed"] for r in results)}


def scheduled_one():
    from .ai import configured, generate, quota
    from .config import settings

    with DB.begin() as db:
        candidate = db.scalar(
            select(EvaluationSchedule)
            .where(EvaluationSchedule.enabled.is_(True), EvaluationSchedule.status == "idle", EvaluationSchedule.next_run_at <= now())
            .limit(1)
        )
        if not candidate:
            return False
        t = db.scalar(select(Tenant).where(Tenant.id == candidate.tenant_id).with_for_update())
        schedule = db.scalar(
            select(EvaluationSchedule)
            .where(EvaluationSchedule.tenant_id == t.id, EvaluationSchedule.status == "idle")
            .with_for_update(skip_locked=True)
        )
        if not schedule:
            return False
        profile = db.get(AIProfile, t.id)
        if not profile or profile.paused or not configured() or t.status != "approved" or t.billing_status != "active":
            schedule.next_run_at = now() + timedelta(days=1)
            return True
        cases = decrypt(schedule.cases)["cases"]
        # Whole-suite reservation avoids a partially billed run when the allowance is low.
        daily = db.scalars(
            select(Audit).where(
                Audit.tenant_id == t.id,
                Audit.action == "ai.request",
                Audit.created_at >= now().replace(hour=0, minute=0, second=0, microsecond=0),
            )
        ).all()
        if len(daily) + len(cases) > settings.ai_daily_requests:
            schedule.next_run_at = now() + timedelta(days=1)
            schedule.last_result = encrypt({"passed": False, "skipped": "allowance_unavailable"})
            return True
        for case in cases:
            quota(db, t.id)
        profiles = [knowledge_profile(db, profile, case["question"]) for case in cases]
        schedule.status = "running"
        tenant_id = t.id
    result = evaluate_cases(cases, profiles, generate)
    with DB.begin() as db:
        db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        schedule = db.get(EvaluationSchedule, tenant_id)
        schedule.last_result = encrypt(result)
        schedule.status, schedule.next_run_at = "idle", now() + timedelta(days=1)
        schedule.failures = 0 if result["passed"] else schedule.failures + 1
        if schedule.failures >= 2:
            db.get(AIProfile, tenant_id).paused = True
            db.add(Audit(tenant_id=tenant_id, actor="quality", action="ai.regression.paused"))
        db.add(Audit(tenant_id=tenant_id, actor="quality", action="quality.completed", detail="passed" if result["passed"] else "failed"))
    return True
