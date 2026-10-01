# 0.3.0

- Tenant AI profiles and SMS drafts with bounded model context, durable worker and owner review.
- Inbound speech/DTMF receptionist, AI disclosure, encrypted session history, replay-safe turns and human forwarding fallback.
- Per-tenant daily request quotas and whole-call completion accounting.
- AI activation UI, migration, architecture and security tests. Live provider verification remains outstanding.

# Changelog

## 0.2.0 — 2026-10-01

Commercial launch hardening: verified account onboarding and encrypted transactional email outbox; one-use recovery links and session revocation; authenticator MFA with replay protection; current StripeClient billing integration, invoice/Checkout webhooks and periodic reconciliation; tenant-locked monthly SMS/call allowances and call completion ledger; Alembic initial/upgrade migrations; production HTTPS proxy, encrypted backup scripts and readiness endpoint. Actual live account access, deployment and acceptance are still required.

## 0.1.0 — 2026-10-01

Initial pilot implementation: architecture and runbooks; tenant accounts; operator regulatory review; live UK number search and durable provisioning; Stripe fixed subscriptions; threaded SMS inbox; signed callbacks; consent/opt-outs; UK forwarding; CI and Docker deployment definition. Public launch and AI/WhatsApp/RCS/calendar/email integrations remain gated by the roadmap.
