import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import settings


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = 'tenants'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    legal_name: Mapped[str] = mapped_column(String(200), default='')
    address: Mapped[str] = mapped_column(Text, default='')
    registration_number: Mapped[str] = mapped_column(String(80), default='')
    terms_version: Mapped[str] = mapped_column(String(40), default='')
    status: Mapped[str] = mapped_column(String(30), default='pending')
    twilio_sid: Mapped[str | None] = mapped_column(String(40), unique=True)
    credentials: Mapped[str] = mapped_column(Text, default='')
    bundle_sid: Mapped[str] = mapped_column(String(40), default='')
    address_sid: Mapped[str] = mapped_column(String(40), default='')
    bundle_type: Mapped[str] = mapped_column(String(20), default='')
    stripe_customer: Mapped[str | None] = mapped_column(String(100), unique=True)
    subscription: Mapped[str | None] = mapped_column(String(100), unique=True)
    checkout_sid: Mapped[str] = mapped_column(String(100), default='')
    checkout_plan: Mapped[str] = mapped_column(String(30), default='')
    billing_status: Mapped[str] = mapped_column(String(30), default='unpaid')
    plan: Mapped[str] = mapped_column(String(30), default='connect')
    spend_limit: Mapped[int] = mapped_column(Integer, default=2000)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default='owner')
    platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)


class Session(Base):
    __tablename__ = 'sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Number(Base):
    __tablename__ = 'numbers'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'), index=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True)
    sid: Mapped[str] = mapped_column(String(40), unique=True)
    sms: Mapped[bool] = mapped_column(Boolean, default=False)
    voice: Mapped[bool] = mapped_column(Boolean, default=False)
    whatsapp: Mapped[str] = mapped_column(String(20), default='not_registered')
    rcs: Mapped[str] = mapped_column(String(20), default='not_registered')
    forwarding: Mapped[str] = mapped_column(String(20), default='')


class Order(Base):
    __tablename__ = 'orders'
    __table_args__ = (UniqueConstraint('tenant_id', 'request_key'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'), index=True)
    phone: Mapped[str] = mapped_column(String(20))
    number_type: Mapped[str] = mapped_column(String(20))
    request_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default='queued')
    error: Mapped[str] = mapped_column(Text, default='')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Message(Base):
    __tablename__ = 'messages'
    __table_args__ = (UniqueConstraint('tenant_id', 'request_key'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'), index=True)
    number_id: Mapped[str] = mapped_column(ForeignKey('numbers.id'))
    peer: Mapped[str] = mapped_column(String(30))
    direction: Mapped[str] = mapped_column(String(10))
    channel: Mapped[str] = mapped_column(String(20), default='sms')
    body: Mapped[str] = mapped_column(Text)
    sid: Mapped[str | None] = mapped_column(String(40), unique=True)
    request_key: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default='queued')
    error: Mapped[str] = mapped_column(String(80), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Suppression(Base):
    __tablename__ = 'suppressions'
    __table_args__ = (UniqueConstraint('tenant_id', 'peer'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'))
    peer: Mapped[str] = mapped_column(String(30))


class Event(Base):
    __tablename__ = 'events'
    id: Mapped[str] = mapped_column(String(150), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'))
    kind: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Audit(Base):
    __tablename__ = 'audit'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'), index=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RateBucket(Base):
    __tablename__ = 'rate_buckets'
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


engine = create_engine(settings.database_url, pool_pre_ping=True,
                       **({'connect_args': {'check_same_thread': False}} if settings.database_url.startswith('sqlite') else {}))
DB = sessionmaker(engine, expire_on_commit=False)
