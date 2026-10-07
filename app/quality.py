"""Opt-in daily quality checks; known regressions pause automation."""

from datetime import timedelta
from time import monotonic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from .models import AIProfile, Audit, DB, EvaluationSchedule, Tenant, now
from .autonomy import EvaluationSuite, knowledge_profile, owner
from .security import csrf, current_user, decrypt, encrypt

router = APIRouter()


class ScheduleInput(EvaluationSuite):
    enabled: bool = False
    minimum_pass_rate: int = Field(default=100, ge=1, le=100)
    max_latency_ms: int = Field(default=8000, ge=500, le=30000)
    pause_after_failures: int = Field(default=2, ge=1, le=5)
    pause_on_forbidden: bool = True


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
        schedule.cases = encrypt({
            "cases": [c.model_dump() for c in data.cases],
            "policy": {
                "minimum_pass_rate": data.minimum_pass_rate,
                "max_latency_ms": data.max_latency_ms,
                "pause_after_failures": data.pause_after_failures,
                "pause_on_forbidden": data.pause_on_forbidden,
            },
        })
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
            "policy": (decrypt(schedule.cases).get("policy") if schedule.cases else None) or {
                "minimum_pass_rate": 100,
                "max_latency_ms": 8000,
                "pause_after_failures": 2,
                "pause_on_forbidden": True,
            },
        }


def evaluate_cases(cases, profiles, generate, policy=None):
    policy = policy or {}
    max_latency_ms = int(policy.get("max_latency_ms", 8000))
    minimum_pass_rate = int(policy.get("minimum_pass_rate", 100))
    results = []
    for case, profile in zip(cases, profiles):
        started = monotonic()
        try:
            answer = generate(profile, [{"role": "user", "content": case["question"]}])
            missing = [term for term in case.get("expected_terms", []) if term.lower() not in answer.lower()]
            forbidden = [term for term in case.get("forbidden_terms", []) if term.lower() in answer.lower()]
            latency_ms = round((monotonic() - started) * 1000)
            latency_failed = latency_ms > max_latency_ms
            results.append(
                {
                    "reply": answer,
                    "passed": not missing and not forbidden and not latency_failed,
                    "missing": missing,
                    "forbidden": forbidden,
                    "latency_ms": latency_ms,
                    "latency_failed": latency_failed,
                }
            )
        except Exception:
            results.append({"passed": False, "error": "model_unavailable"})
    passed_count = sum(1 for r in results if r.get("passed"))
    pass_rate = round((passed_count / len(results)) * 100) if results else 0
    forbidden_failures = sum(1 for r in results if r.get("forbidden"))
    unavailable_failures = sum(1 for r in results if r.get("error") == "model_unavailable")
    return {
        "results": results,
        "passed": pass_rate >= minimum_pass_rate,
        "pass_rate": pass_rate,
        "minimum_pass_rate": minimum_pass_rate,
        "forbidden_failures": forbidden_failures,
        "unavailable_failures": unavailable_failures,
    }


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
        stored = decrypt(schedule.cases)
        cases = stored["cases"]
        policy = stored.get("policy") or {
            "minimum_pass_rate": 100,
            "max_latency_ms": 8000,
            "pause_after_failures": 2,
            "pause_on_forbidden": True,
        }
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
    result = evaluate_cases(cases, profiles, generate, policy)
    with DB.begin() as db:
        db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        schedule = db.get(EvaluationSchedule, tenant_id)
        schedule.last_result = encrypt(result)
        schedule.status, schedule.next_run_at = "idle", now() + timedelta(days=1)
        schedule.failures = 0 if result["passed"] else schedule.failures + 1
        should_pause = (
            (bool(policy.get("pause_on_forbidden", True)) and result.get("forbidden_failures", 0) > 0)
            or schedule.failures >= int(policy.get("pause_after_failures", 2))
        )
        if should_pause:
            profile = db.get(AIProfile, tenant_id)
            if profile and not profile.paused:
                profile.paused = True
                reason = "forbidden_content" if result.get("forbidden_failures", 0) else "repeated_regression"
                db.add(Audit(tenant_id=tenant_id, actor="quality", action="ai.regression.paused", detail=reason))
        db.add(Audit(tenant_id=tenant_id, actor="quality", action="quality.completed", detail="passed" if result["passed"] else "failed"))
    return True
