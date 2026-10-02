# Raeburn Communications

UK business number provisioning, call forwarding and a tenant-isolated threaded SMS inbox, backed by customer-specific Twilio subaccounts and Stripe subscriptions.

**Status: commercial launch implementation; live provider configuration and acceptance remain outstanding.** Live credentials, approved customer bundles, Stripe prices and deployment are not configured by committing this repository. WhatsApp, RCS, business email and calendar actions remain roadmap items. AI SMS drafts and turn-based inbound voice are implemented but are not live features until provider setup and acceptance tests are completed.

See [production launch handoff](docs/LAUNCH.md), [full architecture](docs/ARCHITECTURE.md), [operations](docs/OPERATIONS.md) and [delivery roadmap](docs/ROADMAP.md).

## Run locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
# Edit .env, then load it into this shell:
set -a
. ./.env
set +a
python -m alembic upgrade head
python -m uvicorn app.main:app --reload
```

Open http://localhost:8000. Set `REGISTRATION_ENABLED=true` for local development. Production registration also requires complete `PUBLIC_SALES_ENABLED` configuration. Use a 12+ character password. Provider-free mode serves the portal and authentication; availability search, payment and activation fail explicitly until configured. No fake inventory or fake payment approvals are shown.

Generate and set `ENCRYPTION_KEY` before connecting Twilio:

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Run the worker in a second shell with the same environment:

```bash
python -m app.worker
```

For operator access, promote a registered account using a protected shell:

```bash
python -m app.manage promote-admin operator@example.com
```

## Production configuration

Use `ENVIRONMENT=production`, an HTTPS `PUBLIC_URL`, PostgreSQL `DATABASE_URL` using `postgresql+psycopg://`, and a durable encryption key. SQLite is rejected in production. Registration is closed by default.

Docker Compose includes API, worker, initial-schema job and PostgreSQL. Configure `.env` with `POSTGRES_PASSWORD` and a database URL pointing to `db:5432`; use a TLS reverse proxy in front of port 8000. Do not expose PostgreSQL publicly.

```bash
docker compose up --build -d
```

Do not market the app or enable paid public registration until the launch gates in the roadmap are complete. Stripe prices should reflect measured costs and agreed pilot terms; this repository does not assume the earlier example prices are viable.

## Webhooks

| Provider | URL | Notes |
| --- | --- | --- |
| Twilio inbound SMS | `/webhooks/twilio/inbound` | Attached when number is provisioned |
| Twilio SMS delivery | `/webhooks/twilio/status` | Attached to each outbound message |
| Twilio incoming call | `/webhooks/twilio/voice` | Attached when number is provisioned |
| Stripe | `/webhooks/stripe` | Configure subscription, invoice and Checkout lifecycle events in the launch handoff and signing secret |

Twilio signatures are checked using each customer's Auth Token and `PUBLIC_URL`. Stripe signatures use the raw payload. Webhook URLs must be publicly reachable over HTTPS. Do not point RCS callbacks to the SMS handler; RCS is a separate future integration.

## Checks

```bash
python -m ruff check app tests
python -m pytest -q
node --check app/static/app.js
```

Tests cover authentication, CSRF, tenant isolation, purchase gates/idempotency, opt-outs, webhook replay/signatures, delivery-state ordering, provisioning reconciliation and ambiguous outbound failures. External calls are mocked; tests do not purchase numbers, send SMS or charge cards.

MIT licensed.

Production adds email verification/recovery, administrator TOTP MFA, current-state invoice reconciliation, monthly SMS/call allowances, versioned migrations, HTTPS proxy configuration and encrypted backup scripts. `/ready` deliberately returns 503 until required live configuration and worker heartbeat are present.

## AI receptionist

Version 0.3 adds tenant-specific SMS reply drafts and inbound turn-based voice AI, disabled by default. See [AI architecture and activation](docs/AI_ARCHITECTURE.md). No live model or telephony deployment is configured yet.

## Autonomous service (0.4)

Owner-enabled automatic replies, approved knowledge, conversation takeover/pause, internal department booking calendars, customer-confirmed booking/cancellation/rescheduling, requested email outbox, lead capture, operational receipts and regression evaluation are implemented. ConversationRelay streaming and approved WhatsApp/RCS bindings are available in code, with live activation prerequisites. See [autonomous delivery status](docs/AUTONOMY.md) for the complete scope and remaining work.
