"""Durable worker. Run separately: python -m app.worker.

Never blindly retry an ambiguous outbound send: Twilio Messaging create has no
application idempotency key. An operator reconciles sending/review records.
"""
import logging
import os
import smtplib
import socket
from email.message import EmailMessage
import time
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import or_, select
from .config import settings
from .ai import draft_one
from .models import Audit, DB, EmailJob, Message, Number, Order, Suppression, Tenant, WorkerHeartbeat, now
from .security import decrypt
from .billing import periodic_reconcile
from .providers import tenant_client

log = logging.getLogger(__name__)


def within_budget(client, tenant):
    rows = client.usage.records.this_month.list(category='totalprice', limit=1)
    if not rows:
        raise RuntimeError('Usage totals unavailable; sending paused')
    if rows[0].price_unit.lower() != 'usd':
        raise RuntimeError('Unsupported provider billing currency')
    return abs(Decimal(str(rows[0].price))) * 100 < tenant.spend_limit


def provision_one():
    with DB.begin() as db:
        order = db.scalar(select(Order).where(or_(Order.status == 'queued',
            (Order.status == 'processing') & (Order.lease_until < now())))
            .order_by(Order.created_at).with_for_update(skip_locked=True).limit(1))
        if not order:
            return False
        order.status, order.lease_until = 'processing', now() + timedelta(minutes=5)
        order.attempts += 1
        order_id = order.id
    try:
        with DB.begin() as db:
            order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
            t = db.get(Tenant, order.tenant_id)
            if t.status != 'approved' or t.billing_status != 'active' or t.bundle_type != order.number_type:
                raise RuntimeError('Customer approval or subscription is no longer active')
            client = tenant_client(t)
            bundle = client.numbers.v2.regulatory_compliance.bundles(t.bundle_sid).fetch()
            if bundle.status != 'twilio-approved':
                raise RuntimeError('Regulatory approval is no longer valid')
            existing = client.incoming_phone_numbers.list(phone_number=order.phone, limit=2)
            if len(existing) > 1:
                raise RuntimeError('Duplicate provider resources require reconciliation')
            kwargs = {'sms_url': settings.public_url + '/webhooks/twilio/inbound', 'sms_method': 'POST',
                      'voice_url': settings.public_url + '/webhooks/twilio/voice', 'voice_method': 'POST',
                      'status_callback': settings.public_url + '/webhooks/twilio/voice-status', 'status_callback_method': 'POST',
                      'friendly_name': 'raeburn-order:' + order.id}
            if existing:
                remote = existing[0]
                if remote.friendly_name != kwargs['friendly_name']:
                    raise RuntimeError('Existing number does not belong to this order')
                remote = client.incoming_phone_numbers(remote.sid).update(**kwargs)
            else:
                kwargs.update(phone_number=order.phone, bundle_sid=t.bundle_sid)
                if t.address_sid:
                    kwargs['address_sid'] = t.address_sid
                remote = client.incoming_phone_numbers.create(**kwargs)
            saved = db.scalar(select(Number).where(Number.phone == order.phone))
            if saved and saved.tenant_id != t.id:
                raise RuntimeError('Number ownership conflict')
            if not saved:
                db.add(Number(tenant_id=t.id, phone=remote.phone_number, sid=remote.sid,
                    sms=remote.capabilities.get('sms', False), voice=remote.capabilities.get('voice', False)))
            order.status, order.error, order.lease_until = 'active', '', None
            db.add(Audit(tenant_id=t.id, actor='worker', action='number.provisioned', detail=order.id))
    except Exception:
        log.error('Provisioning order %s requires review', order_id)
        with DB.begin() as db:
            order = db.get(Order, order_id)
            order.status, order.error, order.lease_until = 'review', 'Provisioning needs operator reconciliation. No automatic purchase retry.', None
    return True


def send_one():
    with DB.begin() as db:
        message = db.scalar(select(Message).where(Message.direction == 'outbound', Message.status == 'queued')
                            .order_by(Message.created_at).with_for_update(skip_locked=True).limit(1))
        if not message:
            return False
        message.status = 'sending'
        message_id = message.id
    try:
        with DB.begin() as db:
            m = db.scalar(select(Message).where(Message.id == message_id).with_for_update())
            t, n = db.get(Tenant, m.tenant_id), db.get(Number, m.number_id)
            suppression = db.scalar(select(Suppression).where(Suppression.tenant_id == t.id, Suppression.peer == m.peer))
            if t.billing_status != 'active' or t.status != 'approved' or suppression or t.plan == 'business':
                m.status, m.error = 'failed', 'account_or_consent_blocked'
                return True
            client = tenant_client(t)
            if not within_budget(client, t):
                m.status, m.error = 'failed', 'spend_limit'
                return True
            remote = client.messages.create(to=m.peer, from_=n.phone, body=m.body,
                status_callback=settings.public_url + '/webhooks/twilio/status')
            m.sid, m.status = remote.sid, remote.status
    except Exception:
        log.error('Outbound message %s requires reconciliation', message_id)
        with DB.begin() as db:
            m = db.get(Message, message_id)
            m.status, m.error = 'review', 'provider_result_unknown'
    return True



def heartbeat():
    identity = socket.gethostname() + ':' + str(os.getpid())
    with DB.begin() as db:
        row = db.get(WorkerHeartbeat, identity)
        if row:
            row.seen_at = now()
        else:
            db.add(WorkerHeartbeat(id=identity, seen_at=now()))


def email_one():
    if not settings.smtp_host:
        return False
    with DB.begin() as db:
        job = db.scalar(select(EmailJob).where(EmailJob.status == 'queued').order_by(EmailJob.created_at)
                        .with_for_update(skip_locked=True).limit(1))
        if not job:
            return False
        job.status = 'sending'
        job_id = job.id
    try:
        with DB() as db:
            job = db.get(EmailJob, job_id)
            payload = decrypt(job.encrypted_payload)
            message = EmailMessage()
            message['From'], message['To'], message['Subject'] = settings.email_from, job.recipient, payload['subject']
            message.set_content(payload['body'])
            connection = smtplib.SMTP_SSL if settings.smtp_port == 465 else smtplib.SMTP
            with connection(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
                if settings.smtp_port != 465:
                    smtp.starttls()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(message)
        with DB.begin() as db:
            job = db.get(EmailJob, job_id)
            job.status, job.encrypted_payload = 'sent', ''
    except Exception:
        log.error('Transactional email %s requires review', job_id)
        with DB.begin() as db:
            db.get(EmailJob, job_id).status = 'review'
    return True

def run():
    settings.validate()
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            heartbeat()
            if settings.stripe_key:
                periodic_reconcile()
            worked = draft_one()
            worked = email_one() or worked
            worked = provision_one() or worked
            worked = send_one() or worked
            if not worked:
                time.sleep(2)
        except Exception:
            log.error('Worker cycle failed; inspect secure provider dashboards')
            time.sleep(5)


if __name__ == '__main__':
    run()
