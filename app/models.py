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
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    legal_name: Mapped[str] = mapped_column(String(200), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    registration_number: Mapped[str] = mapped_column(String(80), default="")
    terms_version: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    twilio_sid: Mapped[str | None] = mapped_column(String(40), unique=True)
    credentials: Mapped[str] = mapped_column(Text, default="")
    bundle_sid: Mapped[str] = mapped_column(String(40), default="")
    address_sid: Mapped[str] = mapped_column(String(40), default="")
    bundle_type: Mapped[str] = mapped_column(String(20), default="")
    stripe_customer: Mapped[str | None] = mapped_column(String(100), unique=True)
    subscription: Mapped[str | None] = mapped_column(String(100), unique=True)
    checkout_sid: Mapped[str] = mapped_column(String(100), default="")
    checkout_plan: Mapped[str] = mapped_column(String(30), default="")
    billing_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    billing_status: Mapped[str] = mapped_column(String(30), default="unpaid")
    plan: Mapped[str] = mapped_column(String(30), default="connect")
    spend_limit: Mapped[int] = mapped_column(Integer, default=2000)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="owner")
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_secret: Mapped[str] = mapped_column(Text, default="")
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_counter: Mapped[int] = mapped_column(Integer, default=-1)
    mfa_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)


class Session(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    mfa_authenticated: Mapped[bool] = mapped_column(Boolean, default=False)


class Number(Base):
    __tablename__ = "numbers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True)
    sid: Mapped[str] = mapped_column(String(40), unique=True)
    sms: Mapped[bool] = mapped_column(Boolean, default=False)
    voice: Mapped[bool] = mapped_column(Boolean, default=False)
    whatsapp: Mapped[str] = mapped_column(String(20), default="not_registered")
    rcs: Mapped[str] = mapped_column(String(20), default="not_registered")
    whatsapp_sender: Mapped[str] = mapped_column(String(40), default="")
    rcs_service_sid: Mapped[str] = mapped_column(String(40), default="")
    rcs_sender: Mapped[str] = mapped_column(String(200), default="")
    forwarding: Mapped[str] = mapped_column(String(20), default="")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("tenant_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    phone: Mapped[str] = mapped_column(String(20))
    number_type: Mapped[str] = mapped_column(String(20))
    request_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("tenant_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    number_id: Mapped[str] = mapped_column(ForeignKey("numbers.id"))
    peer: Mapped[str] = mapped_column(String(30))
    direction: Mapped[str] = mapped_column(String(10))
    channel: Mapped[str] = mapped_column(String(20), default="sms")
    segment_units: Mapped[int] = mapped_column(Integer, default=0)
    sensitive_payload: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text)
    sid: Mapped[str | None] = mapped_column(String(40), unique=True)
    request_key: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    error: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Suppression(Base):
    __tablename__ = "suppressions"
    __table_args__ = (UniqueConstraint("tenant_id", "peer"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    peer: Mapped[str] = mapped_column(String(30))


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(150), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    kind: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RateBucket(Base):
    __tablename__ = "rate_buckets"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    hide_parameters=True,
    **({"connect_args": {"check_same_thread": False}} if settings.database_url.startswith("sqlite") else {}),
)
DB = sessionmaker(engine, expire_on_commit=False)


class ActionToken(Base):
    __tablename__ = "action_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class EmailJob(Base):
    __tablename__ = "email_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    recipient: Mapped[str] = mapped_column(String(254))
    encrypted_payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Call(Base):
    __tablename__ = "calls"
    sid: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    number_id: Mapped[str] = mapped_column(ForeignKey("numbers.id"))
    destination: Mapped[str] = mapped_column(String(20))
    reserved_minutes: Mapped[int] = mapped_column(Integer)
    billed_minutes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="reserved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AIProfile(Base):
    __tablename__ = "ai_profiles"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    voice_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    autonomous: Mapped[bool] = mapped_column(Boolean, default=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str] = mapped_column(String(20), default="en-GB")
    business_info: Mapped[str] = mapped_column(Text, default="")
    greeting: Mapped[str] = mapped_column(String(500), default="Hello, you are speaking with our AI receptionist. How can I help?")


class AIJob(Base):
    __tablename__ = "ai_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), unique=True)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    reply: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class VoiceSession(Base):
    __tablename__ = "voice_sessions"
    sid: Mapped[str] = mapped_column(ForeignKey("calls.sid"), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    history: Mapped[str] = mapped_column(Text, default="")
    turn: Mapped[int] = mapped_column(Integer, default=0)
    token: Mapped[str] = mapped_column(String(64))
    previous_token: Mapped[str] = mapped_column(String(64), default="")
    last_xml: Mapped[str] = mapped_column(Text, default="")
    pending_action_id: Mapped[str] = mapped_column(String(36), default="")
    status: Mapped[str] = mapped_column(String(30), default="active")


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("tenant_id", "number_id", "channel", "peer"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    number_id: Mapped[str] = mapped_column(ForeignKey("numbers.id"))
    channel: Mapped[str] = mapped_column(String(20), default="sms")
    peer: Mapped[str] = mapped_column(String(30))
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id", name="fk_conversation_customer"), index=True)
    mode: Mapped[str] = mapped_column(String(20), default="ai")
    assigned_to: Mapped[str] = mapped_column(String(36), default="")
    reason: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Knowledge(Base):
    __tablename__ = "knowledge"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(1000), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500), default="")
    timezone: Mapped[str] = mapped_column(String(100), default="Europe/London")
    duration: Mapped[int] = mapped_column(Integer, default=30)
    opens: Mapped[int] = mapped_column(Integer, default=9)
    closes: Mapped[int] = mapped_column(Integer, default=17)
    weekdays: Mapped[str] = mapped_column(String(20), default="0,1,2,3,4")
    integration_id: Mapped[str | None] = mapped_column(ForeignKey("integrations.id", name="fk_department_integration"))
    bookings_enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (UniqueConstraint("tenant_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"))
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id", name="fk_booking_customer"), index=True)
    peer: Mapped[str] = mapped_column(String(30))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="confirmed")
    provider_id: Mapped[str] = mapped_column(String(100), default="")
    request_key: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ActionJob(Base):
    __tablename__ = "action_jobs"
    __table_args__ = (UniqueConstraint("tenant_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    kind: Mapped[str] = mapped_column(String(30))
    confirmation_message_id: Mapped[str] = mapped_column(String(36), default="")
    payload: Mapped[str] = mapped_column(Text)
    request_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="awaiting_confirmation")
    receipt: Mapped[str] = mapped_column(Text, default="")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Lead(Base):
    __tablename__ = "leads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), unique=True)
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Integration(Base):
    __tablename__ = "integrations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(100))
    encrypted_config: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_status: Mapped[str] = mapped_column(String(30), default="unverified")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    preferences: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class IdentityChallenge(Base):
    __tablename__ = "identity_challenges"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class CatalogItem(Base):
    __tablename__ = "catalog_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    integration_id: Mapped[str] = mapped_column(ForeignKey("integrations.id"))
    name: Mapped[str] = mapped_column(String(100))
    price_id: Mapped[str] = mapped_column(String(100))
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="gbp")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PaymentRequest(Base):
    __tablename__ = "payment_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("action_jobs.id"), unique=True)
    catalog_id: Mapped[str] = mapped_column(ForeignKey("catalog_items.id"))
    session_id: Mapped[str] = mapped_column(String(200), unique=True)
    url: Mapped[str] = mapped_column(Text)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(30), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class KnowledgeRevision(Base):
    __tablename__ = "knowledge_revisions"
    __table_args__ = (UniqueConstraint("knowledge_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    knowledge_id: Mapped[str] = mapped_column(ForeignKey("knowledge.id"))
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FinancialEntry(Base):
    __tablename__ = "financial_entries"
    __table_args__ = (UniqueConstraint("tenant_id", "reference"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(Integer)
    period: Mapped[str] = mapped_column(String(7))
    reference: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CustomerAPIKey(Base):
    __tablename__ = "customer_api_keys"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), unique=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    scopes: Mapped[str] = mapped_column(String(100))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EvaluationSchedule(Base):
    __tablename__ = "evaluation_schedules"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    cases: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="idle")
    last_result: Mapped[str] = mapped_column(Text, default="")
    failures: Mapped[int] = mapped_column(Integer, default=0)


class WebhookJob(Base):
    __tablename__ = "webhook_jobs"
    __table_args__ = (UniqueConstraint("integration_id", "action_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    integration_id: Mapped[str] = mapped_column(ForeignKey("integrations.id"))
    action_id: Mapped[str] = mapped_column(ForeignKey("action_jobs.id"))
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
