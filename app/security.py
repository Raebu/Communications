import base64
import hashlib
import hmac
import json
import secrets
from datetime import timedelta
from cryptography.fernet import Fernet
from fastapi import HTTPException, Request
from sqlalchemy import select
from .config import settings
from .models import DB, RateBucket, Session, User, now


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return base64.b64encode(salt + digest).decode()


def verify_password(password, encoded):
    raw = base64.b64decode(encoded)
    return hmac.compare_digest(raw[16:], hashlib.scrypt(password.encode(), salt=raw[:16], n=16384, r=8, p=1))


def encrypt(value):
    if not settings.encryption_key:
        raise HTTPException(503, 'Configure ENCRYPTION_KEY before connecting providers')
    return Fernet(settings.encryption_key.encode()).encrypt(json.dumps(value).encode()).decode()


def decrypt(value):
    return json.loads(Fernet(settings.encryption_key.encode()).decrypt(value.encode()))


def current_user(request: Request):
    token = request.cookies.get('session', '')
    with DB() as db:
        session = db.get(Session, hashlib.sha256(token.encode()).hexdigest())
        if not session or session.expires_at.replace(tzinfo=now().tzinfo) < now():
            raise HTTPException(401, 'Sign in required')
        user = db.get(User, session.user_id)
        if settings.environment == 'production':
            allowed = {'/api/me', '/api/logout', '/api/security/mfa/setup', '/api/security/mfa/confirm'}
            if request.url.path not in allowed:
                if not user.email_verified:
                    raise HTTPException(403, 'Verify your email before continuing')
                if user.mfa_enabled and not session.mfa_authenticated:
                    raise HTTPException(403, 'Sign in again using your authenticator')
                if user.platform_admin and not user.mfa_enabled:
                    raise HTTPException(403, 'Enable authenticator MFA before accessing administration')
        return user


def csrf(request: Request):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if request.headers.get('origin') != settings.public_url:
            raise HTTPException(403, 'Invalid request origin')
        if request.headers.get('x-requested-with') != 'Raeburn':
            raise HTTPException(403, 'Request verification required')


def rate_limit(key, maximum=20):
    # Upsert first so first-use races do not create duplicate rows.
    with DB.begin() as db:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        insert = pg_insert if db.bind.dialect.name == 'postgresql' else sqlite_insert
        db.execute(insert(RateBucket).values(key=key, count=0, expires_at=now() + timedelta(minutes=1))
                   .on_conflict_do_nothing(index_elements=['key']))
        row = db.scalar(select(RateBucket).where(RateBucket.key == key).with_for_update())
        if row.expires_at.replace(tzinfo=now().tzinfo) < now():
            row.count, row.expires_at = 1, now() + timedelta(minutes=1)
        elif row.count >= maximum:
            raise HTTPException(429, 'Too many requests; try again in one minute')
        else:
            row.count += 1


def totp_code(secret, counter):
    import struct
    raw = base64.b32decode(secret)
    digest = hmac.new(raw, struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = int.from_bytes(digest[offset:offset + 4], 'big') & 0x7fffffff
    return str(value % 1000000).zfill(6)


def verify_totp(secret, code, last_counter=-1, timestamp=None):
    import time
    counter = int((time.time() if timestamp is None else timestamp) // 30)
    for step in (counter - 1, counter, counter + 1):
        if step > last_counter and hmac.compare_digest(totp_code(secret, step), code):
            return step
    return None
