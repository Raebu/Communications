"""Answered-call recording retention.

Recording media remains with the telephony provider. Raeburn stores only the
recording SID and encrypted metadata, then deletes provider media after the
retention period captured when the recording was created.
"""
from datetime import timedelta

from sqlalchemy import select
from twilio.base.exceptions import TwilioRestException

from .models import Audit, CustomerEvent, DB, Tenant, now
from .providers import tenant_client
from .security import decrypt, encrypt


def _aware(value):
    return value if value.tzinfo else value.replace(tzinfo=now().tzinfo)


def recording_retention_one():
    with DB.begin() as db:
        rows = db.scalars(
            select(CustomerEvent)
            .where(CustomerEvent.kind == "call.recording")
            .order_by(CustomerEvent.occurred_at)
            .with_for_update(skip_locked=True)
            .limit(100)
        ).all()
        for event in rows:
            if not event.encrypted_payload:
                continue
            try:
                payload = decrypt(event.encrypted_payload)
            except Exception:
                continue
            if payload.get("deleted") or payload.get("status") not in {"completed", ""}:
                continue
            days = max(1, min(90, int(payload.get("retention_days", 30))))
            if _aware(event.occurred_at) + timedelta(days=days) > now():
                continue
            recording_sid = str(payload.get("recording_sid", ""))
            tenant = db.get(Tenant, event.tenant_id)
            if not tenant or not recording_sid:
                continue
            try:
                tenant_client(tenant).recordings(recording_sid).delete()
            except TwilioRestException as error:
                if error.status != 404:
                    raise
            payload["deleted"] = True
            payload["deleted_at"] = now().isoformat()
            event.encrypted_payload = encrypt(payload)
            db.add(
                Audit(
                    tenant_id=event.tenant_id,
                    actor="system",
                    action="call.recording.deleted",
                    detail=recording_sid,
                )
            )
            return True
    return False
