"""Approved sender bindings. No implicit sender registration or cross-tenant fallback."""

from datetime import timedelta
from sqlalchemy import select
from .models import Message, now


def enabled(number, channel):
    return (
        number.sms
        if channel == "sms"
        else bool(number.whatsapp == "approved" and number.whatsapp_sender)
        if channel == "whatsapp"
        else bool(number.rcs == "approved" and number.rcs_service_sid)
        if channel == "rcs"
        else False
    )


def arguments(db, m, n):
    if not enabled(n, m.channel):
        raise RuntimeError("Sender is not approved")
    if m.channel == "whatsapp":
        latest = db.scalar(
            select(Message)
            .where(
                Message.tenant_id == m.tenant_id,
                Message.number_id == n.id,
                Message.peer == m.peer,
                Message.channel == "whatsapp",
                Message.direction == "inbound",
                Message.created_at >= now() - timedelta(hours=24),
            )
            .limit(1)
        )
        if not latest:
            raise RuntimeError("WhatsApp window closed; approved template required")
        return {"to": "whatsapp:" + m.peer, "from_": n.whatsapp_sender}
    if m.channel == "rcs":
        return {"to": m.peer, "messaging_service_sid": n.rcs_service_sid}
    return {"to": m.peer, "from_": n.phone}
