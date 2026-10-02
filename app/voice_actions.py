"""Call-scoped action proposals. Caller ID never grants access to customer history."""

import hashlib
import json
import re
from datetime import timedelta, timezone
from types import SimpleNamespace
from sqlalchemy import select
from .models import AIProfile, ActionJob, Call, DB, Tenant, VoiceSession, now, uid
from .autonomy import action_one, autonomous_answer, conversation, knowledge_profile


def invalidate_proposal(sid):
    with DB.begin() as db:
        session = db.get(VoiceSession, sid)
        if session and session.pending_action_id:
            job = db.get(ActionJob, session.pending_action_id)
            if job and job.status == "awaiting_confirmation":
                job.status = "cancelled"
            session.pending_action_id = ""


def decision(sid, speech, history):
    from .ai import generate

    execute_id = None
    with DB.begin() as db:
        call = db.get(Call, sid)
        if not call:
            raise RuntimeError("Call unavailable")
        t = db.scalar(select(Tenant).where(Tenant.id == call.tenant_id).with_for_update())
        session = db.scalar(select(VoiceSession).where(VoiceSession.sid == sid).with_for_update())
        p = db.get(AIProfile, t.id)
        if (
            not p
            or not p.enabled
            or not p.autonomous
            or not p.voice_enabled
            or p.paused
            or t.status != "approved"
            or t.billing_status != "active"
            or t.plan == "business"
            or session.status != "streaming"
            or call.status == "completed"
        ):
            raise RuntimeError("Voice authority unavailable")
        peer = "voice:" + hashlib.sha256(sid.encode()).hexdigest()[:24]
        m = SimpleNamespace(id=uid(), tenant_id=t.id, number_id=call.number_id, channel="voice", peer=peer, body=speech)
        c = conversation(db, m)
        if c.mode != "ai":
            return "A person needs to continue this enquiry. Press zero for a person."
        if speech.lower().startswith("verify code "):
            from .identity import redeem

            code = re.sub(r"[^0-9]", "", speech[12:])
            return redeem(db, t, c, code) if len(code) == 8 else "Say verify code followed by eight digits."
        pending = db.get(ActionJob, session.pending_action_id) if session.pending_action_id else None
        confirm = re.sub(r"[^a-z ]", "", speech.lower()).strip() == "confirm this action"
        if confirm:
            if (
                not pending
                or pending.status != "awaiting_confirmation"
                or pending.conversation_id != c.id
                or pending.created_at.replace(tzinfo=timezone.utc) < now() - timedelta(minutes=2)
            ):
                return "There is no current action to confirm. Please describe your request again."
            pending.status = "queued"
            execute_id = pending.id
            session.pending_action_id = ""
        else:
            if pending and pending.status == "awaiting_confirmation":
                pending.status = "cancelled"
            session.pending_action_id = ""
            profile = knowledge_profile(db, p, speech)
            result = autonomous_answer(db, t, m, profile, history, generate)
            job = db.scalar(select(ActionJob).where(ActionJob.tenant_id == t.id, ActionJob.request_key == "action:" + m.id))
            if job:
                session.pending_action_id = job.id
                # The same operation-specific preview as messaging; no spoken UUIDs.
                return result.split(". Reply CONFIRM ")[0] + ". To authorise exactly this request, say confirm this action."
            return result
    # Committed consent precedes execution. The worker can also claim this same durable job.
    action_one(execute_id)
    with DB() as db:
        job = db.get(ActionJob, execute_id)
        if job.status != "completed":
            return "Your request needs outcome review. I cannot confirm success. Please ask for a person."
        receipt = json.loads(job.receipt)
        if job.kind in {"book", "reschedule"}:
            return "Your appointment is confirmed for " + receipt["starts_at"] + "."
        if job.kind == "cancel":
            return "Your appointment has been cancelled."
        if job.kind == "email":
            return "The email has been queued for delivery. Delivery is not yet confirmed."
        return "Your enquiry has been recorded for our team."
