import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from .config import settings
from .models import ActionToken, Audit, DB, EmailJob, Session, User, now
from .security import csrf, current_user, decrypt, encrypt, hash_password, rate_limit, verify_totp

router = APIRouter(prefix='/api/security')


def queue_action(db, user, purpose):
    token = secrets.token_urlsafe(48)
    db.add(ActionToken(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id,
        purpose=purpose, expires_at=now() + timedelta(minutes=30)))
    label = 'Verify your email' if purpose == 'verify' else 'Reset your password'
    url = settings.public_url + '/?action=' + purpose + '&token=' + token
    db.add(EmailJob(recipient=user.email, encrypted_payload=encrypt({
        'subject': label + ' — Raeburn Communications',
        'body': label + ':\n\n' + url + '\n\nThis link expires in 30 minutes. If you did not request this, ignore this message.',
    })))


class EmailRequest(BaseModel):
    email: EmailStr


@router.post('/request/{purpose}', dependencies=[Depends(csrf)])
def request_action(purpose: str, data: EmailRequest, request: Request):
    if purpose not in {'verify', 'reset'}:
        raise HTTPException(404, 'Unknown account action')
    rate_limit('account-action:' + request.client.host, 5)
    if not settings.smtp_host or not settings.encryption_key:
        raise HTTPException(503, 'Account email is not configured')
    with DB.begin() as db:
        user = db.scalar(select(User).where(User.email == str(data.email).lower()))
        if user and (purpose == 'reset' or not user.email_verified):
            queue_action(db, user, purpose)
    return {'status': 'If this account is eligible, an email will arrive shortly.'}


class ConsumeAction(BaseModel):
    token: str = Field(min_length=40, max_length=100)
    password: str = Field(default='', max_length=128)


@router.post('/complete/{purpose}', dependencies=[Depends(csrf)])
def consume_action(purpose: str, data: ConsumeAction, request: Request):
    rate_limit('account-complete:' + request.client.host, 15)
    with DB.begin() as db:
        action = db.scalar(select(ActionToken).where(ActionToken.token_hash == hashlib.sha256(data.token.encode()).hexdigest()).with_for_update())
        if not action or action.used or action.purpose != purpose or action.expires_at.replace(tzinfo=now().tzinfo) < now():
            raise HTTPException(400, 'This link is invalid or expired')
        user = db.get(User, action.user_id)
        if purpose == 'reset':
            if len(data.password) < 12:
                raise HTTPException(422, 'Password must contain at least 12 characters')
            user.password = hash_password(data.password)
            db.execute(delete(Session).where(Session.user_id == user.id))
        elif purpose != 'verify':
            raise HTTPException(404, 'Unknown account action')
        user.email_verified, action.used = True, True
        db.add(Audit(tenant_id=user.tenant_id, actor=user.id, action='account.' + purpose))
    return {'status': 'completed'}


@router.post('/mfa/setup', dependencies=[Depends(csrf)])
def mfa_setup(user=Depends(current_user)):
    with DB.begin() as db:
        current = db.scalar(select(User).where(User.id == user.id).with_for_update())
        if current.mfa_enabled:
            raise HTTPException(409, 'MFA is already enabled; contact support for recovery')
        secret = base64.b32encode(secrets.token_bytes(20)).decode()
        current.mfa_secret = encrypt({'secret': secret})
        current.mfa_started_at = now()
        uri = 'otpauth://totp/Raeburn%20Communications:' + quote(current.email, safe='') + '?secret=' + secret + '&issuer=Raeburn%20Communications'
        return {'secret': secret, 'uri': uri}


class MFACode(BaseModel):
    code: str = Field(pattern=r'^\d{6}$')


@router.post('/mfa/confirm', dependencies=[Depends(csrf)])
def mfa_confirm(data: MFACode, user=Depends(current_user)):
    rate_limit('mfa-confirm:' + user.id, 5)
    with DB.begin() as db:
        current = db.scalar(select(User).where(User.id == user.id).with_for_update())
        if current.mfa_enabled or not current.mfa_secret or not current.mfa_started_at:
            raise HTTPException(409, 'Start MFA setup first')
        if current.mfa_started_at.replace(tzinfo=now().tzinfo) < now() - timedelta(minutes=10):
            raise HTTPException(409, 'MFA setup expired')
        counter = verify_totp(decrypt(current.mfa_secret)['secret'], data.code)
        if counter is None:
            raise HTTPException(401, 'Invalid authenticator code')
        current.mfa_counter, current.mfa_enabled = counter, True
        db.execute(delete(Session).where(Session.user_id == user.id))
        db.add(Audit(tenant_id=user.tenant_id, actor=user.id, action='mfa.enabled'))
    return {'status': 'enabled'}
