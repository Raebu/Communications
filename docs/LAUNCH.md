# Production launch handoff

The agreed first commercial product is **UK business numbers, UK call forwarding and SMS**, on the existing Raeburn DigitalOcean account. AI, WhatsApp, RCS and booking are separate later products. The local release has been strengthened for commercial deployment, but a code commit cannot make payments or telephone service live.

## Actual access state

- GitHub repository is connected and writable.
- DigitalOcean connection containing the Raeburn recruitment staging infrastructure is selected. Communications must have a separate production host; do not install onto the recruitment/NATS hosts.
- Stripe exposes **RaeburnBrainAI Development in test mode only**. It cannot take live customer payments through that connection.
- Twilio Developer Kit supplies integration skills, not an authenticated Twilio account connector. No Twilio API credentials are present in this workspace.
- Direct and configured-proxy SSH routes to DigitalOcean are unavailable in this execution environment. No billable Communications droplet was created without a usable deployment route.

## Secure setup required

Use the account dashboards or a trusted secrets manager. Do not paste secrets into chat, issues, workflow logs or git. A browser-assisted setup can be used after authorization if connector tools cannot perform it.

1. Select the live Stripe account for The Raeburn Group's contracting business. Create a separate Stripe Product for each subscription tier and monthly GBP Prices based on actual Twilio costs and chosen allowances. Enable Customer Portal cancellation/payment-method management. Use a restricted live API key with permissions for Customer/Checkout/Portal creation and subscription/price reads. Add webhook lifecycle events below. Review VAT status before configuring Stripe Tax; no automatic tax setting is silently assumed.
2. In the funded Twilio parent account, create a **Main API key** for subaccount/account-key lifecycle. Standard keys cannot create/manage Accounts and Keys. Store its SID and secret only in the production secret store. Child account API keys and webhook tokens remain encrypted in PostgreSQL. Existing Raeburn numbers are not moved into this service automatically.
3. Configure a TLS transactional SMTP sender with a verified domain and SPF/DKIM/DMARC. Registration verification and password recovery use an encrypted durable email outbox.
4. Set the actual contracting business's legal name/address/support email, published terms and privacy URLs, terms version, price IDs and included monthly allowances. Proposed initial defaults are 100 conservative SMS segments and 30 forwarding minutes per calendar month; public pricing must be approved against real costs. The service offers no outgoing/emergency calls.
5. Choose the branded hostname, create a separate London production server, point DNS to it, and install Docker Engine with Compose v2.36+ (the production override uses `!reset`). Keep database ports private; restrict SSH to administrative sources.
6. Clone this repository on that server and create `.env` with mode 0600. Generate PostgreSQL password and Fernet key locally, retain them in the secret manager. Use `DATABASE_URL=postgresql+psycopg://communications:<password>@db:5432/communications` and `POSTGRES_PASSWORD=<same password>`. Avoid credentials containing URL-special characters unless encoded correctly.
7. Set `ENVIRONMENT=production`, `SERVICE_HOSTNAME=<hostname>`, `PUBLIC_URL=https://<hostname>`. Keep `PUBLIC_SALES_ENABLED=false` and `REGISTRATION_ENABLED=false` until acceptance tests and final service policies are ready. Build and migrate:

```bash
bash deployment/deploy.sh
```

8. Configure live Twilio and Stripe callbacks against the canonical HTTPS hostname. Keep public registration closed and run `docker compose run --rm api python -m app.manage bootstrap-admin operator@example.com --company "Your legal business"` in the protected server console. Set the operator password interactively, sign in and enroll an authenticator before accessing administrative operations. After the actual commercial setup is complete, set both public sales and registration to true; the application rejects incomplete live configuration.
9. Complete the real acceptance tests below. Open sales only after they pass.

## Webhooks

| Provider | URL / events |
| --- | --- |
| SMS inbound | `/webhooks/twilio/inbound` |
| SMS delivery | `/webhooks/twilio/status` |
| Voice routing | `/webhooks/twilio/voice` |
| Forwarding completion | `/webhooks/twilio/voice-status` |
| Stripe | `/webhooks/stripe`; `customer.subscription.created`, `.updated`, `.deleted`, `invoice.paid`, `invoice.payment_failed`, `checkout.session.completed`, `checkout.session.async_payment_succeeded` |

Provider signatures are required. Set Stripe endpoint API version to the integration's SDK version and rehearse the current invoice `parent.subscription_details` graph. Both webhook events and five-minute worker reconciliation retrieve current subscription/invoice state.

## Operational differences from the initial pilot

- Email verification gates production customer operations.
- TOTP MFA is available for customers and required for platform administrators. Enrollment revokes prior sessions, and login codes cannot be replayed. Production sessions must have actually passed MFA when it is enabled.
- Alembic migrations version the initial schema and commercial changes. For a database created by the old pilot, back it up, verify it matches revision 0001, then **stamp 0001 and upgrade head**; never run a blind fresh-schema migration on an existing schema.
- Monthly SMS allowances are reserved when a send request is committed. They conservatively count UCS-2 units, including failed/review sends. Local daily cap is also enforced. No overage billing is advertised or enabled.
- Forwarding reserves up to ten minutes from a shared monthly allowance under a tenant row lock. Completion rounds connected duration up to minutes. Missing callbacks retain the reservation for safe reconciliation. The allowance covers forwarded duration; provider inbound/ringing costs and delayed financial totals still require provider usage alerts and abuse monitoring.
- `/health` checks database reachability. `/ready` also requires a live worker, provider configuration and public-sales switch. It is a configuration/heartbeat check, not a substitute for genuine provider calls.
- Worker exceptions avoid logging provider payloads/secrets. Database parameter logging is hidden. Caddy access logs omit headers, query strings and remote IP.
- Encrypted database backups use `age`; they must be copied to an off-site store with tested restore and agreed retention. DigitalOcean image backups alone are not a database restore plan.

## Live acceptance evidence required

- HTTPS hostname resolves; database/worker survive a restart; encrypted backup restores to a clean database.
- Genuine customer can register, receive verification, recover password and use an authenticator. Administrative access fails without MFA.
- Customer legal identity matches a Twilio-approved GB business bundle for the chosen number type.
- A real paid Stripe subscription activates the account; failed renewal/cancellation blocks sends and forwarding; webhook outage is recovered by reconciliation.
- Available number is purchased once and attached to correct tenant/subaccount/bundle. Provider/local crash recovery does not buy twice.
- Real inbound SMS appears under the correct thread; authorized outbound SMS is delivered and signed status received; STOP blocks all subsequent manual/queued sends.
- A call forwards to a permitted UK destination; repeat webhook does not reserve twice; allowance prevents excess concurrent forwarding; completion updates usage.
- Customer sees accurate recurring pricing, allowances, support, cancellation/porting and privacy terms. Number release/refund/porting remain explicit operator procedures.

Until those tests are recorded, this repository is prepared for live deployment but the service must not be described as live.
