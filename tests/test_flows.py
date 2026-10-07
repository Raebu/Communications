from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import select
from twilio.request_validator import RequestValidator
from app.config import settings
from app.main import app
from app.models import DB, Event, Message, Number, Order, Suppression, Tenant
from app.security import encrypt
from app.worker import provision_one, send_one
from app.company_verification import release_waiting_orders

HEADERS = {'origin': 'http://localhost:8000', 'x-requested-with': 'Raeburn'}




def customer(email='one@example.com'):
    c = TestClient(app)
    result = c.post('/api/register', json={'email':email,'password':'correct-horse-battery','company':'Example Ltd','accept_terms':True}, headers=HEADERS)
    assert result.status_code == 200
    assert c.post('/api/login', json={'email':email,'password':'correct-horse-battery'}, headers=HEADERS).status_code == 200
    t = c.get('/api/me').json()['tenant']['id']
    return c, t


def enable(t):
    with DB.begin() as db:
        tenant = db.get(Tenant,t)
        tenant.status, tenant.billing_status = 'approved', 'active'
        tenant.twilio_sid, tenant.bundle_sid, tenant.bundle_type = 'AC' + t.replace('-','')[:32], 'BU'+'a'*32, 'Local'
        tenant.credentials = encrypt({'key_sid':'SKtest','key_secret':'secret','auth_token':'testtoken'})


def number(t, phone='+442080001001'):
    with DB.begin() as db:
        n = Number(tenant_id=t,phone=phone,sid='PN'+t.replace('-','')[:32],sms=True,voice=True)
        db.add(n)
        db.flush()
        return n.id


def test_auth_and_origin():
    c,t=customer()
    assert c.get('/api/me').status_code == 200
    assert c.put('/api/profile',json={'legal_name':'Acme','address':'12 London Road','registration_number':'123'}).status_code == 403
    assert c.post('/api/logout',headers=HEADERS).status_code == 200
    assert c.get('/api/me').status_code == 401


def test_registration_closed():
    old=settings.registration_enabled
    settings.registration_enabled=False
    try:
        assert TestClient(app).post('/api/register',json={'email':'new@example.com','password':'correct-horse-battery','company':'Example','accept_terms':True},headers=HEADERS).status_code==403
    finally:
        settings.registration_enabled=old


def test_purchase_gates_and_idempotency():
    c,t=customer()
    data={'phone':'+442080001001','type':'Local','request_key':'1234567890abcdef'}
    assert c.post('/api/orders',json=data,headers=HEADERS).status_code==402
    enable(t)
    a=c.post('/api/orders',json=data,headers=HEADERS)
    b=c.post('/api/orders',json=data,headers=HEADERS)
    assert a.json()['id']==b.json()['id']
    data['phone']='+442080001002'
    assert c.post('/api/orders',json=data,headers=HEADERS).status_code==409
    with DB() as db:
        assert len(db.scalars(select(Order)).all())==1



def test_preferred_number_waits_for_telephone_approval_and_never_provisions_early(monkeypatch):
    c,t=customer()
    with DB.begin() as db:
        tenant=db.get(Tenant,t)
        tenant.status='pending'
        tenant.billing_status='active'
    data={'phone':'+442080001001','type':'Local','request_key':'1234567890abcdef'}
    response=c.post('/api/orders',json=data,headers=HEADERS)
    assert response.status_code==200
    assert response.json()['status']=='awaiting_approval'
    assert 'availability cannot be guaranteed' in response.json()['message'].lower()

    client=MagicMock()
    monkeypatch.setattr('app.worker.tenant_client',lambda tenant:client)
    assert provision_one() is False
    client.incoming_phone_numbers.create.assert_not_called()
    with DB() as db:
        assert db.scalar(select(Order)).status=='awaiting_approval'


def test_waiting_number_is_released_only_after_matching_telephone_approval():
    c,t=customer()
    with DB.begin() as db:
        tenant=db.get(Tenant,t)
        tenant.status='pending'
        tenant.billing_status='active'
        order=Order(
            tenant_id=t,
            phone='+442080001001',
            number_type='Local',
            request_key='1234567890abcdef',
            status='awaiting_approval',
        )
        db.add(order)

    with DB.begin() as db:
        tenant=db.get(Tenant,t)
        assert release_waiting_orders(db,tenant)==0
        assert db.scalar(select(Order)).status=='awaiting_approval'

    with DB.begin() as db:
        tenant=db.get(Tenant,t)
        tenant.status='approved'
        tenant.bundle_type='Local'
        assert release_waiting_orders(db,tenant)==1
        assert db.scalar(select(Order)).status=='queued'

def test_cross_tenant_isolation():
    c,t=customer()
    other,other_t=customer('two@example.com')
    enable(t)
    n=number(other_t)
    assert c.get('/api/numbers').json()==[]
    data={'number_id':n,'peer':'+447700900000','body':'Hello','request_key':'1234567890abcdef','consent_confirmed':True}
    assert c.post('/api/messages',json=data,headers=HEADERS).status_code==404
    assert c.put(f'/api/numbers/{n}/forwarding',json={'destination':'+447700900000'},headers=HEADERS).status_code==404
    assert c.get('/api/admin/tenants').status_code==403


def twilio_post(c,path,t,params):
    with DB() as db:
        params['AccountSid']=db.get(Tenant,t).twilio_sid
    signature=RequestValidator('testtoken').compute_signature(settings.public_url+path,params)
    return c.post(path,data=params,headers={'x-twilio-signature':signature})


def test_signed_inbound_duplicate_and_stop():
    c,t=customer()
    enable(t)
    n=number(t)
    p={'To':'+442080001001','From':'+447700900000','Body':'STOP','MessageSid':'SM'+'1'*32}
    assert c.post('/webhooks/twilio/inbound',data=p).status_code==403
    assert twilio_post(c,'/webhooks/twilio/inbound',t,p).status_code==200
    assert twilio_post(c,'/webhooks/twilio/inbound',t,p).status_code==200
    with DB() as db:
        assert len(db.scalars(select(Message)).all())==1
        assert len(db.scalars(select(Event)).all())==1
        assert len(db.scalars(select(Suppression)).all())==1
    assert c.post('/api/messages',json={'number_id':n,'peer':p['From'],'body':'hi','request_key':'1234567890abcdef','consent_confirmed':True},headers=HEADERS).status_code==409


def test_message_status_does_not_regress():
    c,t=customer()
    enable(t)
    n=number(t)
    with DB.begin() as db:
        db.add(Message(tenant_id=t,number_id=n,peer='+447700900000',body='hi',direction='outbound',sid='SMtest',status='sent'))
    assert twilio_post(c,'/webhooks/twilio/status',t,{'MessageSid':'SMtest','MessageStatus':'delivered'}).status_code==204
    assert twilio_post(c,'/webhooks/twilio/status',t,{'MessageSid':'SMtest','MessageStatus':'sent'}).status_code==204
    with DB() as db:
        assert db.scalar(select(Message)).status=='delivered'


def test_provision_reconciles_existing_number(monkeypatch):
    c,t=customer()
    enable(t)
    response=c.post('/api/orders',json={'phone':'+442080001001','type':'Local','request_key':'1234567890abcdef'},headers=HEADERS)
    client=MagicMock()
    client.numbers.v2.regulatory_compliance.bundles.return_value.fetch.return_value.status='twilio-approved'
    remote=MagicMock(sid='PNtest',phone_number='+442080001001',capabilities={'sms':True,'voice':True},friendly_name='raeburn-order:'+response.json()['id'])
    client.incoming_phone_numbers.list.return_value=[remote]
    client.incoming_phone_numbers.return_value.update.return_value=remote
    monkeypatch.setattr('app.worker.tenant_client',lambda tenant:client)
    assert provision_one()
    client.incoming_phone_numbers.create.assert_not_called()
    assert c.get('/api/orders').json()[0]['status']=='active'
    assert len(c.get('/api/numbers').json())==1


def test_ambiguous_send_never_blindly_retries(monkeypatch):
    c,t=customer()
    enable(t)
    n=number(t)
    assert c.post('/api/messages',json={'number_id':n,'peer':'+447700900000','body':'hi','request_key':'1234567890abcdef','consent_confirmed':True},headers=HEADERS).status_code==200
    client=MagicMock()
    client.messages.create.side_effect=TimeoutError()
    monkeypatch.setattr('app.worker.tenant_client',lambda tenant:client)
    monkeypatch.setattr('app.worker.within_budget',lambda client,tenant:True)
    assert send_one()
    assert not send_one()
    client.messages.create.assert_called_once()
    with DB() as db:
        assert db.scalar(select(Message)).status=='review'


def test_profile_update_invalidates_approval():
    c,t=customer()
    enable(t)
    assert c.put('/api/profile',json={'legal_name':'New legal entity','address':'12 New London Road','registration_number':'123'},headers=HEADERS).status_code==200
    assert c.get('/api/me').json()['tenant']['status']=='pending'


def test_stripe_invalid_signature():
    settings.stripe_key='sk_test_example'
    settings.stripe_webhook_secret='whsec_example'
    assert TestClient(app).post('/webhooks/stripe',content='{}',headers={'stripe-signature':'bad'}).status_code==403


def test_subscription_webhook_requires_paid_invoice_and_uses_current_state(monkeypatch):
    c,t=customer()
    with DB.begin() as db:
        db.get(Tenant,t).stripe_customer='cus_test'
    settings.stripe_key='sk_test'
    settings.stripe_webhook_secret='whsec_test'
    settings.connect_price='price_connect'
    event={'id':'evt_test','type':'customer.subscription.updated','data':{'object':{'id':'sub_test','status':'active'}}}
    current={'id':'sub_test','customer':'cus_test','metadata':{'tenant_id':t},'items':{'data':[{'price':{'id':'price_connect'}}]},'status':'active','latest_invoice':{'paid':False}}
    monkeypatch.setattr('app.main.stripe.Webhook.construct_event',lambda *args:event)
    client = MagicMock()
    client.v1.subscriptions.retrieve.return_value = current
    monkeypatch.setattr('app.main.billing_client',lambda:client)
    assert c.post('/webhooks/stripe',content='{}').status_code==200
    assert c.get('/api/me').json()['tenant']['billing_status']=='unpaid'
    event['id']='evt_paid'
    current['latest_invoice']['paid']=True
    assert c.post('/webhooks/stripe',content='{}').status_code==200
    assert c.get('/api/me').json()['tenant']['billing_status']=='active'
    # An old payload says active, but current provider state says canceled.
    event['id']='evt_old'
    current['status']='canceled'
    assert c.post('/webhooks/stripe',content='{}').status_code==200
    assert c.get('/api/me').json()['tenant']['billing_status']=='canceled'


def test_send_cap_and_subscription_gate():
    c,t=customer()
    enable(t)
    n=number(t)
    with DB.begin() as db:
        for i in range(100):
            db.add(Message(tenant_id=t,number_id=n,peer='+447700900000',body='hi',direction='outbound',status='sent',request_key=str(i)))
    data={'number_id':n,'peer':'+447700900000','body':'hi','request_key':'1234567890abcdef','consent_confirmed':True}
    assert c.post('/api/messages',json=data,headers=HEADERS).status_code==429
    with DB.begin() as db:
        db.get(Tenant,t).billing_status='canceled'
    assert c.post('/api/messages',json=data,headers=HEADERS).status_code==409


def test_voice_requires_signature_and_active_account(monkeypatch):
    c,t=customer()
    enable(t)
    n=number(t)
    assert c.put(f'/api/numbers/{n}/forwarding',json={'destination':'+447700900000'},headers=HEADERS).status_code==200
    monkeypatch.setattr('app.main.within_budget',lambda client,tenant:True)
    monkeypatch.setattr('app.main.tenant_client',lambda tenant:MagicMock())
    params={'To':'+442080001001','From':'+447700900001','CallSid':'CAtest'}
    assert c.post('/webhooks/twilio/voice',data=params).status_code==403
    assert '<Dial' in twilio_post(c,'/webhooks/twilio/voice',t,params).text
    with DB.begin() as db:
        db.get(Tenant,t).billing_status='canceled'
    assert '<Dial' not in twilio_post(c,'/webhooks/twilio/voice',t,params).text
