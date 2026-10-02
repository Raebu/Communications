## 0.5.0

- Confirmed call-scoped voice actions; interruptions revoke proposals and verified channels control private appointment access.
- Single-use cross-channel proof, encrypted shared preferences, unlink/forget controls and redacted verification outbox.
- Tenant merchant approved GBP catalogue, hosted Checkout and signed/provider-retrieved payment receipts.
- Durable signed business hooks with pinned public IP/TLS and scoped, expiring CRM API keys.
- Immutable knowledge revisions, draft text/HTML imports, opt-in daily regression checks and automatic pause after repeated failure.
- Owner action review/cancellation, invoice-backed finance ledger, portal controls, retention and expanded privacy export.
- Migrations 0008–0013 and integration/identity/payment/quality/delivery tests.

## Unreleased

- Durable, explicitly consented SMS appointment reminders with UK quiet hours, duplicate protection and stale-booking cancellation.
- Owner-only department updates and corrected operations review counts.
- Migration 0007 and reminder consent/delivery suppression tests.

# 0.4.0 — 2026-10-02

Autonomous message decision engine, durable customer-confirmed actions, department booking calendars, knowledge approval/expiry/versioning, staff takeover, global pause, operational dashboard, requested SMTP emails and leads. Added encrypted tenant Google Calendar integration with live free/busy checks and stable event IDs for booking/cancellation/rescheduling. Added signed ConversationRelay WebSocket streaming, interrupt cancellation and fallback; approved WhatsApp/RCS sender bindings with WhatsApp window checks; regression evaluation API and tenant export. Live configuration and several expanded scope items remain outstanding, as recorded in docs/AUTONOMY.md.

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
