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
        return db.get(User, session.user_id)


def csrf(request: Request):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if request.headers.get('origin') != settings.public_url:
            raise HTTPException(403, 'Invalid request origin')
        if request.headers.get('x-requested-with') != 'Raeburn':
            raise HTTPException(403, 'Request verification required')


def rate_limit(key, maximum=20):
    # Durable counters shared by all API replicas; lock existing row in PostgreSQL.
    with DB.begin() as db:
        row = db.scalar(select(RateBucket).where(RateBucket.key == key).with_for_update())
        if row is None:
            db.add(RateBucket(key=key, count=1, expires_at=now() + timedelta(minutes=1)))
        elif row.expires_at.replace(tzinfo=now().tzinfo) < now():
            row.count, row.expires_at = 1, now() + timedelta(minutes=1)
        elif row.count >= maximum:
            raise HTTPException(429, 'Too many requests; try again in one minute')
        else:
            row.count += 1
