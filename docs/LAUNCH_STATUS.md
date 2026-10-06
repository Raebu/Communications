# Activation handoff — 6 October 2026

The 0.5 application release is published to `Raebu/Communications`. Hosted CI has verified the PostgreSQL test suite, container build, Caddy configuration, script/JavaScript validation and CodeQL analysis. This is evidence about code delivery, not a live service certificate.

## Approved hosting direction

The launch domain is `connect.theraeburngroup.com`. Use the managed-database Docker Compose deployment with Supabase on a dedicated DigitalOcean London host for the portal, API, Twilio callbacks, voice WebSocket endpoint and continuous worker. The connected Vercel account has been inspected; no Communications project was identified. Vercel may host a separate marketing site later. Dedicated DigitalOcean droplet `606614970` is active in London at `178.128.170.162` (2 vCPU, 4 GB RAM, $24/month billed hourly). The application has not been deployed: SSH from this execution environment fails with `Network is unreachable`. DNS configuration remains unverified. See [HOSTING.md](HOSTING.md) and the credential-free [production environment template](../deployment/production.env.example).

## Current blockers

| Dependency | Current evidence | Required activation |
|---|---|---|
| Dedicated Communications host | Dedicated `raeburn-connect-production` droplet `606614970` is active; recruitment hosts are untouched | Reachable deployment access, protected runtime configuration and DNS; see [SERVER_HANDOFF.md](SERVER_HANDOFF.md) |
| Supabase | Raeburn Connect project `yptzmjcevwseddpdwdvb` is healthy in London; private schema `communications` at revision `0014`, owned by login `communications_app`; 34 tables have RLS enabled | Configure the existing login connection privately; verify TLS, runtime transactions, Data API settings and backup restore |
| Twilio | No authenticated provider connector or application credentials are configured here; previous browser verification was blocked by CAPTCHA | Complete verification in your own browser, then securely configure the Main API key/subaccounts and approved sender bindings on the service |
| Platform Stripe | The Live connector now exposes The Raeburn Group of Companies (verified 3 October); it does not configure the application runtime | Authorised live restricted key, webhook and agreed subscription price IDs on the server |
| AI runtime | The repository contains the provider adapter and controls, not a deployed model | Actual approved endpoint/model/key and load/quality acceptance |
| Legal/commercial launch | Public registration/sales and AI-plan checkout remain gated | Final service identity/address/support, published terms/privacy/retention, approved pricing and allowances |
| Email/calendar/merchant integrations | Secure adapters and operator commands are implemented | Authorised credentials, verified senders, webhook configuration and approved products |
| Acceptance and operations | Automated tests pass; no real customer call/payment has been executed by this build | Real calls/interruption/transfer, cross-channel proof, bookings, opt-out/reminders, payment receipt and backup restore tests |

Do not put passwords, provider keys or OAuth refresh tokens in chat or repository files. Configure the protected server environment/credential prompts, then run:

```bash
python -m alembic upgrade head
python -m app.launch
python -m app.manage check-config
```

`app.launch` emits booleans and exits nonzero when configuration is incomplete. It makes no external charge, sends no message and prints no secret. `deployment/deploy-managed-db.sh` performs the managed-database container/config/schema deployment once server access and credentials are available. `deployment/check.sh` checks live API and worker readiness.

## Extensions still outstanding

The exact feature matrix is in [AUTONOMY.md](AUTONOMY.md). Dedicated Microsoft/CRM/email OAuth adapters, PDF/automatic website refresh, approved rich-message templates, automatic metered invoicing, conversion attribution and comprehensive privacy-case/account-deletion workflows are not claimed complete. They must be scoped and validated separately from the implemented core service.
