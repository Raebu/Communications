# Activation handoff — 2 October 2026

The 0.5 application release is published to `Raebu/Communications`. Hosted CI has verified the PostgreSQL test suite, container build, Caddy configuration, script/JavaScript validation and CodeQL analysis. This is evidence about code delivery, not a live service certificate.

## Approved hosting direction

The launch domain is `connect.theraeburngroup.com`. Use the managed-database Docker Compose deployment with Supabase on a dedicated DigitalOcean London host for the portal, API, Twilio callbacks, voice WebSocket endpoint and continuous worker. The connected Vercel account has been inspected; no Communications project was identified. Vercel may host a separate marketing site later. No host, DNS record or Vercel deployment has been created by this handoff. See [HOSTING.md](HOSTING.md) and the credential-free [production environment template](../deployment/production.env.example).

## Current blockers

| Dependency | Current evidence | Required activation |
|---|---|---|
| Dedicated Communications host | The selected DigitalOcean account lists only the two recruitment staging droplets | A separately provisioned host and authorised deployment access; connect.theraeburngroup.com is approved, DNS still needs configuration |
| Supabase | The Raeburn Group organisation is connected; no dedicated Communications project exists | Confirm organisation and provider costs, create the London project, configure its private schema/TLS connection and validate migrations |
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

`app.launch` emits booleans and exits nonzero when configuration is incomplete. It makes no external charge, sends no message and prints no secret. `deployment/deploy.sh` performs container/config/schema deployment once an authorised host exists. `deployment/check.sh` checks live API and worker readiness.

## Extensions still outstanding

The exact feature matrix is in [AUTONOMY.md](AUTONOMY.md). Dedicated Microsoft/CRM/email OAuth adapters, PDF/automatic website refresh, approved rich-message templates, automatic metered invoicing, conversion attribution and comprehensive privacy-case/account-deletion workflows are not claimed complete. They must be scoped and validated separately from the implemented core service.
