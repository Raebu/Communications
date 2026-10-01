# Operator runbook

## First pilot customer

1. Configure the environment and initialize the database. Protect the Fernet key and database backup together; losing the key makes customer credentials unrecoverable.
2. Open registration temporarily for a nominated pilot customer and operator. Close registration again. Promote the operator with the CLI. Use a separate operator account, restricted administrative access and MFA at the surrounding access layer.
3. Customer submits legal identity; operator checks identity and customer contract. Connect their Twilio subaccount in Customer Review.
4. In that subaccount, read current GB regulatory requirements, create the required end-user/documents/bundle and submit it. Wait for actual Twilio approval. Enter approved BU bundle, matching number type and AD address where required. Independently inspect the bundle identity; the API checks provider status/type, not document contents.
5. Configure Stripe recurring prices and hosted billing portal. Configure subscription webhooks and signing secret. Use Stripe test mode for rehearsal and verify that billing becomes active only after a signed subscription event. Agree usage costs and caps with the customer before live charges.
6. Customer subscribes, searches and requests a number. Run worker. Confirm order is active, then verify provider ownership, bundle assignment and webhook URLs.
7. Send a real inbound SMS, inspect thread, send an explicitly authorized reply, inspect delivery callback. Test STOP then confirm queued/manual sends are blocked. Test START. These live acceptance tests are not run automatically.
8. Configure a permitted forwarding destination, place a call and verify two-leg cost, timeout and call duration. Test account suspension/cancellation blocks routing. This app does not provide emergency calling.
9. Review provider usage alerts, queue-age monitoring, backup restore and cancellation/port-out procedure before expanding the pilot.

## Review orders

Inspect Twilio IncomingPhoneNumbers in the customer subaccount. Match the `raeburn-order:<UUID>` tag, phone, SID and bundle. If purchase succeeded remotely, the retry reconciles it instead of buying again. If inventory disappeared, agree a replacement or refund with the customer. Do not release any number merely because an order is in review.

```bash
python -m app.manage retry-order ORDER_UUID
```

Only use after operator investigation. Orders with expired `processing` leases can reconcile automatically. Review orders require explicit CLI retry.

## Review messages

An outbound `sending` record with no persisted SID may be the result of a crash after Twilio accepted the request. A `review` record may also have been accepted. Check provider logs using recipient, originator, timestamp and body. Never blindly requeue: that can send twice. The initial release deliberately requires a protected database/operator reconciliation procedure; a complete operator message-reconciliation UI is a roadmap item.

Status webhook 503 means the local SID was not available yet or is missing; inspect Twilio retries and worker records. Avoid logging credentials, full message contents or identity documents into ordinary application logs. Production logging must redact provider exception bodies.

## Cancellation and incident controls

Subscription cancellation disables future sends and call forwarding after webhook processing. Rental remains payable until numbers are explicitly released or ported. Follow agreed retention/port-out rules. Suspend abusive customer subaccounts in Twilio; then update local status through a controlled operator procedure. No automatic destructive releases are implemented.

Rate and budget controls are deliberately conservative but provider usage reporting can lag. Use provider usage alerts and manual suspension as additional controls. A real-time spend reservation ledger and per-call concurrency controls are required before scaling paid service.

## Credentials and access

Fernet protects stored provider secrets; use a managed secret store for the master credentials/encryption key. A crash during key creation can leave an unused remote API key. Inventory and revoke orphaned keys during reconciliation. Rotate subaccount keys and webhook token together only through a tested procedure. CLI password reset revokes all existing sessions:

```bash
python -m app.manage reset-password customer@example.com
```

No email password reset flow is shipped. Operator access changes must be audited and tightly controlled.

## Schema, backups and deployment

`init-db` creates missing initial tables; it is not a schema migration framework. Before the first change to a live schema, add versioned Alembic migrations, test forward/backward compatibility and create a backup. Encrypt backups; document retention and deletion. Rehearse restoration to a clean instance with the encryption key.

API health checks only the database. Instrument worker heartbeat, oldest queued/processing job, review count, webhook failures and billing/provider drift. Configure signed webhooks against the exact canonical HTTPS hostname, never a preview deployment. API and worker must use the same database, credentials and public URL.
