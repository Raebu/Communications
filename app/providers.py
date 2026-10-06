import logging

from fastapi import HTTPException
from twilio.rest import Client
from .config import settings
from .security import decrypt, encrypt


logger = logging.getLogger(__name__)


def parent_client():
    if settings.twilio_sid and settings.twilio_api_key and settings.twilio_api_secret:
        return Client(settings.twilio_api_key, settings.twilio_api_secret, account_sid=settings.twilio_sid, timeout=15)
    if settings.environment == 'production':
        logger.error('Configure a Twilio Main API key for account provisioning')
        raise HTTPException(503, 'Number activation is temporarily unavailable. Please try again shortly.')
    if not settings.twilio_sid or not settings.twilio_token:
        logger.error('Twilio account provisioning is not configured')
        raise HTTPException(503, 'Number activation is temporarily unavailable. Please try again shortly.')
    return Client(settings.twilio_sid, settings.twilio_token, timeout=15)


def tenant_client(tenant):
    if not tenant.twilio_sid or not tenant.credentials:
        raise HTTPException(409, 'Customer communications account is not connected')
    credentials = decrypt(tenant.credentials)
    return Client(credentials['key_sid'], credentials['key_secret'], account_sid=tenant.twilio_sid, timeout=15)


def create_subaccount(tenant):
    encrypt({})  # Validate encryption configuration before creating remote resources.
    client = parent_client()
    # Reconcile a previous successful remote call if the process died before commit.
    matches = client.api.accounts.list(friendly_name='raeburn:' + tenant.id, limit=2)
    if len(matches) > 1:
        raise HTTPException(409, 'Multiple matching subaccounts require operator reconciliation')
    account = matches[0] if matches else client.api.accounts.create(friendly_name='raeburn:' + tenant.id)
    account = client.api.accounts(account.sid).fetch()
    key = client.api.accounts(account.sid).new_keys.create(friendly_name='Raeburn platform')
    tenant.twilio_sid = account.sid
    tenant.credentials = encrypt({'key_sid': key.sid, 'key_secret': key.secret, 'auth_token': account.auth_token})
