"""Signed integration events with pinned public IP, TLS hostname verification and bounded delivery."""

import hashlib
import hmac
import http.client
import ipaddress
import json
import socket
import ssl
import subprocess
import sys
from datetime import timedelta, timezone
from urllib.parse import urlparse
from sqlalchemy import select
from fastapi import APIRouter, Depends
from .models import AIProfile, Audit, DB, Integration, Lead, Tenant, WebhookJob, now
from .security import current_user, decrypt, encrypt
from .autonomy import owner

router = APIRouter()


def configuration(url, address, secret):
    parsed = urlparse(url)
    ip = ipaddress.ip_address(address)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.port not in {None, 443}
        or parsed.username
        or parsed.password
        or parsed.fragment
        or not ip.is_global
        or len(url) > 1000
        or len(secret) < 32
    ):
        raise ValueError("Use HTTPS on port 443, a pinned public IP and a signing secret of at least 32 characters")
    return {"url": url, "address": address, "secret": secret}


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname, address):
        super().__init__(hostname, port=443, timeout=5, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # No DNS lookup occurs at delivery time. TLS still verifies the configured hostname.
        sock = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def deliver_now(cfg, event_id, payload):
    configuration(cfg["url"], cfg["address"], cfg["secret"])
    parsed = urlparse(cfg["url"])
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    if len(raw) > 16000:
        raise RuntimeError("Event too large")
    timestamp = str(int(now().timestamp()))
    signature = hmac.new(cfg["secret"].encode(), timestamp.encode() + b"." + event_id.encode() + b"." + raw, hashlib.sha256).hexdigest()
    conn = PinnedHTTPS(parsed.hostname, cfg["address"])
    try:
        conn.request(
            "POST",
            (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
            raw,
            {
                "Content-Type": "application/json",
                "X-Raeburn-Event-ID": event_id,
                "X-Raeburn-Timestamp": timestamp,
                "X-Raeburn-Signature": "sha256=" + signature,
            },
        )
        response = conn.getresponse()
        response.read(65537)
        # No redirects, response bodies, cookies or credentials from the destination are retained.
        return response.status
    finally:
        conn.close()


def deliver(cfg, event_id, payload):
    # Kill the isolated sender at the wall-clock deadline, including slow-drip headers/body.
    result = subprocess.run(
        [sys.executable, "-m", "app.outbound_hooks"],
        input=json.dumps({"config": cfg, "event_id": event_id, "payload": payload}),
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    return int(result.stdout.strip())


def enqueue(db, t, action, conversation, receipt):
    for i in db.scalars(
        select(Integration).where(Integration.tenant_id == t.id, Integration.kind == "outbound_webhook", Integration.enabled.is_(True))
    ):
        if db.scalar(select(WebhookJob).where(WebhookJob.integration_id == i.id, WebhookJob.action_id == action.id)):
            continue
        payload = {
            "type": "action.completed",
            "action_id": action.id,
            "kind": action.kind,
            "receipt": receipt,
            "conversation_id": conversation.id,
        }
        if action.kind == "lead":
            lead = db.scalar(select(Lead).where(Lead.conversation_id == conversation.id))
            payload["lead"] = {"id": lead.id, "summary": lead.summary}
        db.add(WebhookJob(tenant_id=t.id, integration_id=i.id, action_id=action.id, payload=encrypt(payload)))


def webhook_one():
    with DB.begin() as db:
        candidate = db.scalar(
            select(WebhookJob).where(WebhookJob.status == "queued", WebhookJob.due_at <= now()).order_by(WebhookJob.due_at).limit(1)
        )
        if not candidate:
            return False
        t = db.scalar(select(Tenant).where(Tenant.id == candidate.tenant_id).with_for_update())
        j = db.scalar(
            select(WebhookJob).where(WebhookJob.id == candidate.id, WebhookJob.status == "queued").with_for_update(skip_locked=True)
        )
        if not j:
            return False
        i, profile = db.get(Integration, j.integration_id), db.get(AIProfile, t.id)
        if (
            not i
            or i.tenant_id != t.id
            or not i.enabled
            or t.status != "approved"
            or t.billing_status != "active"
            or not profile
            or not profile.enabled
            or not profile.autonomous
            or profile.paused
        ):
            j.status = "cancelled"
            return True
        # Never retry a crash after submission without receiver-side idempotency reconciliation.
        j.status, j.attempts = "sending", j.attempts + 1
        cfg, payload, job_id = decrypt(i.encrypted_config), decrypt(j.payload), j.id
    with DB.begin() as db:
        t = db.scalar(select(Tenant).where(Tenant.id == candidate.tenant_id).with_for_update())
        j = db.scalar(select(WebhookJob).where(WebhookJob.id == job_id).with_for_update())
        integration, profile = db.get(Integration, j.integration_id), db.get(AIProfile, t.id)
        if (
            not integration.enabled
            or t.status != "approved"
            or t.billing_status != "active"
            or not profile
            or not profile.enabled
            or not profile.autonomous
            or profile.paused
        ):
            j.status = "cancelled"
            return True
        cfg = decrypt(integration.encrypted_config)
        try:
            status = deliver(cfg, job_id, payload)
        except Exception:
            status = None
        if status is not None and 200 <= status < 300:
            j.status = "acknowledged"
        elif (
            status in {429, 503}
            and cfg.get("receiver_idempotent") is True
            and j.attempts < 3
            and j.created_at.replace(tzinfo=timezone.utc) > now() - timedelta(days=1)
        ):
            j.status, j.due_at = "queued", now() + timedelta(minutes=5 * j.attempts)
        else:
            j.status = "review"
        db.add(Audit(tenant_id=j.tenant_id, actor="worker", action="webhook." + j.status, detail=j.id))
    return True


@router.get("/api/integration-deliveries")
def deliveries(user=Depends(current_user)):
    owner(user)
    with DB() as db:
        return [
            {"id": j.id, "action_id": j.action_id, "status": j.status, "attempts": j.attempts, "created_at": j.created_at}
            for j in db.scalars(
                select(WebhookJob).where(WebhookJob.tenant_id == user.tenant_id).order_by(WebhookJob.created_at.desc()).limit(200)
            )
        ]


if __name__ == "__main__":
    data = json.loads(sys.stdin.read(32001))
    print(deliver_now(data["config"], data["event_id"], data["payload"]))
