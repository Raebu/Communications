"""Durable agent hunting for real and virtual call queues.

Provider call creation is intentionally not retried after an ambiguous result.
A ticket marked review requires reconciliation before another outbound agent
attempt can be made.
"""
from decimal import Decimal

from sqlalchemy import or_, select

from .config import settings
from .models import Audit, DB, Number, QueueTicket, Tenant, now
from .providers import tenant_client
from .security import decrypt, encrypt


ACTIVE_AGENT_STATES = {"dialing_agent", "agent_calling"}


def _within_budget(client, tenant):
    rows = client.usage.records.this_month.list(category="totalprice", limit=1)
    if not rows:
        raise RuntimeError("Usage totals unavailable; queue hunting paused")
    if rows[0].price_unit.lower() != "usd":
        raise RuntimeError("Unsupported provider billing currency")
    return abs(Decimal(str(rows[0].price))) * 100 < tenant.spend_limit


def _payload(ticket):
    return decrypt(ticket.encrypted_payload) if ticket.encrypted_payload else {}


def _pick_destination(payload):
    members = list(payload.get("members") or [])
    attempted = list(payload.get("attempted") or [])
    remaining = [member for member in members if member not in attempted]
    if not remaining:
        attempted = []
        remaining = members
    if not remaining:
        return "", attempted
    return remaining[0], attempted


def queue_hunt_one():
    with DB.begin() as db:
        candidates = db.scalars(
            select(QueueTicket)
            .where(
                QueueTicket.status.in_(["waiting", "virtual_waiting"]),
                or_(QueueTicket.next_attempt_at.is_(None), QueueTicket.next_attempt_at <= now()),
            )
            .order_by(QueueTicket.entered_at)
            .with_for_update(skip_locked=True)
            .limit(50)
        ).all()
        ticket = None
        payload = None
        destination = ""
        attempted = []
        for candidate in candidates:
            active = db.scalar(
                select(QueueTicket.id).where(
                    QueueTicket.queue_name == candidate.queue_name,
                    QueueTicket.status.in_(ACTIVE_AGENT_STATES),
                ).limit(1)
            )
            if active:
                continue
            candidate_payload = _payload(candidate)
            destination, attempted = _pick_destination(candidate_payload)
            if not destination:
                continue
            ticket = candidate
            payload = candidate_payload
            break
        if not ticket:
            return False

        tenant = db.get(Tenant, ticket.tenant_id)
        number = db.get(Number, ticket.number_id)
        if (
            not tenant
            or not number
            or not number.voice
            or tenant.status != "approved"
            or tenant.billing_status != "active"
        ):
            ticket.status = "attention"
            ticket.updated_at = now()
            return True

        client = tenant_client(tenant)
        if not _within_budget(client, tenant):
            ticket.status = "attention"
            ticket.updated_at = now()
            db.add(Audit(
                tenant_id=ticket.tenant_id,
                actor="system",
                action="call_queue.hunt_paused",
                detail="spend_limit",
            ))
            return True

        attempted.append(destination)
        payload["attempted"] = attempted
        payload["resume_status"] = ticket.status
        ticket.current_destination = destination
        ticket.attempts += 1
        ticket.status = "dialing_agent"
        ticket.updated_at = now()
        ticket.next_attempt_at = None
        ticket.encrypted_payload = encrypt(payload)
        ticket_id = ticket.id
        tenant_id = ticket.tenant_id
        number_phone = number.phone

    submitted = False
    try:
        with DB() as db:
            ticket = db.get(QueueTicket, ticket_id)
            tenant = db.get(Tenant, tenant_id)
            payload = _payload(ticket)
            destination = ticket.current_destination
            client = tenant_client(tenant)
        submitted = True
        remote = client.calls.create(
            to=destination,
            from_=number_phone,
            url=settings.public_url + "/webhooks/twilio/queue-agent?ticket=" + ticket_id,
            method="POST",
            status_callback=settings.public_url + "/webhooks/twilio/queue-agent-status?ticket=" + ticket_id,
            status_callback_method="POST",
            status_callback_event=["completed"],
        )
        with DB.begin() as db:
            ticket = db.get(QueueTicket, ticket_id)
            if ticket.status != "dialing_agent":
                return True
            ticket.provider_sid = remote.sid
            ticket.status = "agent_calling"
            ticket.updated_at = now()
            db.add(Audit(
                tenant_id=ticket.tenant_id,
                actor="worker",
                action="call_queue.agent_hunt_started",
                detail=ticket.id,
            ))
    except Exception:
        with DB.begin() as db:
            ticket = db.get(QueueTicket, ticket_id)
            if ticket:
                ticket.status = "review" if submitted else payload.get("resume_status", "waiting")
                ticket.updated_at = now()
                db.add(Audit(
                    tenant_id=ticket.tenant_id,
                    actor="worker",
                    action="call_queue.agent_hunt_review" if submitted else "call_queue.agent_hunt_blocked",
                    detail=ticket.id,
                ))
    return True
