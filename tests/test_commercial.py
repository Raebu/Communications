import hashlib
import subprocess
import sys
import tempfile
from datetime import timedelta
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from app.config import settings
from app.main import app
from app.models import ActionToken, Call, DB, EmailJob, Session, User, now
from app.security import decrypt, hash_password, totp_code, verify_totp
from test_flows import HEADERS, customer, enable, number, twilio_post


@pytest.fixture(autouse=True)
def reset(database):
    yield


def test_recovery_token_is_one_use_and_invalidates_sessions():
    c,t=customer()
    with DB.begin() as db:
        user=db.scalar(select(User).where(User.tenant_id==t))
        db.add(ActionToken(token_hash=hashlib.sha256(('x'*48).encode()).hexdigest(),user_id=user.id,purpose='reset',expires_at=now()+timedelta(minutes=5)))
    payload={'token':'x'*48,'password':'replacement-password-long'}
    assert c.post('/api/security/complete/reset',json=payload,headers=HEADERS).status_code==200
    assert c.get('/api/me').status_code==401
    assert c.post('/api/security/complete/reset',json=payload,headers=HEADERS).status_code==400
    with DB() as db:
        assert db.scalar(select(User)).email_verified
        assert not db.scalars(select(Session)).all()


def test_email_action_queues_encrypted_token(monkeypatch):
    c,t=customer()
    monkeypatch.setattr(settings,'smtp_host','smtp.example.com')
    assert c.post('/api/security/request/verify',json={'email':'one@example.com'},headers=HEADERS).status_code==200
    with DB() as db:
        job=db.scalar(select(EmailJob))
        assert 'token=' not in job.encrypted_payload
        assert 'token=' in decrypt(job.encrypted_payload)['body']


def test_totp_rfc_and_replay_protection():
    # RFC 6238 SHA1 test secret; its 8-digit code at 59s is 94287082.
    secret='GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
    assert totp_code(secret,1)=='287082'
    assert verify_totp(secret,'287082',timestamp=59)==1
    assert verify_totp(secret,'287082',last_counter=1,timestamp=59) is None


def test_mfa_enrollment_and_login_replay():
    import time
    c,t=customer()
    start=c.post('/api/security/mfa/setup',headers=HEADERS)
    assert start.status_code == 200
    assert start.headers['cache-control'] == 'no-store'
    modules = start.json()['qr_modules']
    assert len(modules) >= 29 and all(len(row) == len(modules) for row in modules)
    assert all(type(cell) is bool for row in modules for cell in row)
    # A four-module white quiet zone is needed for reliable scanning.
    assert all(not any(row) for row in modules[:4] + modules[-4:])
    assert all(not any(row[:4] + row[-4:]) for row in modules)
    assert start.json()['secret'] in start.json()['uri']
    code=totp_code(start.json()['secret'],int(time.time()//30))
    assert c.post('/api/security/mfa/confirm',json={'code':code},headers=HEADERS).status_code==200
    c.post('/api/logout',headers=HEADERS)
    # Enrollment consumed that timestep; reuse must fail.
    assert c.post('/api/login',json={'email':'one@example.com','password':'correct-horse-battery','totp':code},headers=HEADERS).status_code==401


def test_voice_reservations_and_replay_are_bounded(monkeypatch):
    c,t=customer()
    enable(t)
    n=number(t)
    c.put(f'/api/numbers/{n}/forwarding',json={'destination':'+447700900000'},headers=HEADERS)
    monkeypatch.setattr('app.main.within_budget',lambda client,tenant:True)
    monkeypatch.setattr('app.main.tenant_client',lambda tenant:MagicMock())
    monkeypatch.setattr(settings,'voice_monthly_minutes',10)
    params={'To':'+442080001001','From':'+447700900001','CallSid':'CAfirst'}
    assert '<Dial' in twilio_post(c,'/webhooks/twilio/voice',t,params).text
    assert '<Dial' in twilio_post(c,'/webhooks/twilio/voice',t,params).text
    params['CallSid']='CAsecond'
    assert '<Dial' not in twilio_post(c,'/webhooks/twilio/voice',t,params).text
    with DB() as db:
        assert len(db.scalars(select(Call)).all())==1
    callback={'CallSid':'CAfirst','DialCallDuration':'61'}
    assert twilio_post(c,'/webhooks/twilio/voice-status',t,callback).status_code==200
    assert twilio_post(c,'/webhooks/twilio/voice-status',t,callback).status_code==200
    assert c.get('/api/usage').json()['voice_minutes']==2
    assert '<Dial' in twilio_post(c,'/webhooks/twilio/voice',t,params).text


def test_production_sales_configuration_fails_closed(monkeypatch):
    monkeypatch.setattr(settings,'environment','production')
    monkeypatch.setattr(settings,'public_url','https://communications.example.com')
    monkeypatch.setattr(settings,'database_url','postgresql+psycopg://example/db')
    monkeypatch.setattr(settings,'public_sales_enabled',True)
    with pytest.raises(RuntimeError):
        settings.validate()


def test_streaming_body_limit():
    c=TestClient(app)
    assert c.post('/api/login',content=b'x'*65537,headers=HEADERS).status_code==413


def test_migrations_upgrade_existing_customer():
    import os
    path=tempfile.mktemp(suffix='.db')
    environment={**os.environ,'DATABASE_URL':'sqlite:///'+path}
    subprocess.run([sys.executable,'-m','alembic','upgrade','0001'],env=environment,check=True,capture_output=True)
    engine=create_engine('sqlite:///'+path)
    with engine.begin() as connection:
        from sqlalchemy import text
        connection.execute(text("INSERT INTO tenants (id,name,legal_name,address,registration_number,terms_version,status,credentials,bundle_sid,address_sid,bundle_type,billing_status,plan,spend_limit,checkout_sid,checkout_plan,created_at) VALUES ('t','Old Co','','','','draft','pending','','','','','unpaid','connect',2000,'','','2026-10-01')"))
        connection.execute(text("INSERT INTO users (id,tenant_id,email,password,role,platform_admin) VALUES ('u','t','old@example.com',:password,'owner',false)"),{'password':hash_password('old-password-strong')})
    subprocess.run([sys.executable,'-m','alembic','upgrade','head'],env=environment,check=True,capture_output=True)
    assert 'email_verified' in [c['name'] for c in inspect(engine).get_columns('users')]
    with engine.connect() as connection:
        from sqlalchemy import text
        assert connection.execute(text('SELECT email_verified,mfa_enabled,mfa_counter FROM users')).one()==(0,0,-1)


def test_concurrent_purchase_is_one_order():
    from concurrent.futures import ThreadPoolExecutor
    from app.models import Order
    c,t=customer()
    enable(t)
    payload={'phone':'+442080001001','type':'Local','request_key':'concurrent-order-key'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:c.post('/api/orders',json=payload,headers=HEADERS),range(2)))
    assert [r.status_code for r in responses]==[200,200]
    assert responses[0].json()['id']==responses[1].json()['id']
    with DB() as db:
        assert len(db.scalars(select(Order)).all())==1


def test_invoice_event_uses_current_subscription(monkeypatch):
    c,t=customer()
    from app.models import Tenant
    with DB.begin() as db:
        db.get(Tenant,t).stripe_customer='cus_invoice'
    monkeypatch.setattr(settings,'stripe_key','rk_test_fixture')
    monkeypatch.setattr(settings,'stripe_webhook_secret','fixture-signing-secret')
    monkeypatch.setattr(settings,'connect_price','price_connect')
    event={'id':'evt_invoice','type':'invoice.payment_failed','data':{'object':{'parent':{'subscription_details':{'subscription':'sub_invoice'}}}}}
    api=MagicMock()
    api.v1.subscriptions.retrieve.return_value={'id':'sub_invoice','customer':'cus_invoice','items':{'data':[{'price':{'id':'price_connect'},'quantity':1}]},'status':'past_due','latest_invoice':{'status':'open'}}
    monkeypatch.setattr('app.main.billing_client',lambda:api)
    monkeypatch.setattr('app.main.stripe.Webhook.construct_event',lambda *args:event)
    assert c.post('/webhooks/stripe',content='{}').status_code==200
    assert c.get('/api/me').json()['tenant']['billing_status']=='past_due'
    api.v1.subscriptions.retrieve.assert_called_once_with('sub_invoice',{'expand':['latest_invoice']})


def test_company_review_email_is_encrypted_and_unchanged_submission_not_repeated(monkeypatch):
    c, tenant_id = customer()
    monkeypatch.setattr(settings, 'smtp_host', 'smtp.example.com')
    monkeypatch.setattr(settings, 'verification_email_from', 'verification@example.com')
    monkeypatch.setattr(settings, 'company_review_email', 'review@example.com')
    data = {'legal_name': 'Example Limited', 'address': '12 Example Street, London', 'registration_number': '12345678'}
    for _ in range(2):
        assert c.put('/api/profile', json=data, headers=HEADERS).status_code == 200
    with DB() as db:
        jobs = db.scalars(select(EmailJob)).all()
        assert len(jobs) == 2
        assert {job.recipient for job in jobs} == {'one@example.com', 'review@example.com'}
        for job in jobs:
            assert 'awaiting review' not in job.encrypted_payload
            assert decrypt(job.encrypted_payload)['from'] == 'verification@example.com'
        alert = next(job for job in jobs if job.recipient == 'review@example.com')
        assert tenant_id in decrypt(alert.encrypted_payload)['body']
        assert data['address'] not in decrypt(alert.encrypted_payload)['body']


def test_email_delivery_uses_sender_and_support_reply_to(monkeypatch):
    from app.security import encrypt
    from app.worker import email_one
    monkeypatch.setattr(settings, 'smtp_host', 'smtp.example.com')
    monkeypatch.setattr(settings, 'smtp_port', 465)
    monkeypatch.setattr(settings, 'email_from', 'newphoneline@example.com')
    monkeypatch.setattr(settings, 'email_reply_to', 'support@example.com')
    smtp = MagicMock()
    monkeypatch.setattr('app.email_delivery.smtplib.SMTP_SSL', smtp)
    with DB.begin() as db:
        db.add(EmailJob(recipient='review@example.com', encrypted_payload=encrypt({
            'from': 'verification@example.com', 'subject': 'Review', 'body': 'Please review',
        })))
    assert email_one()
    message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
    assert message['From'] == 'verification@example.com'
    assert message['Reply-To'] == 'support@example.com'
    with DB() as db:
        assert db.scalar(select(EmailJob)).status == 'sent'
