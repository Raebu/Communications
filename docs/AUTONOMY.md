# Autonomous communications service — 0.5

This release implements the application workflows below. It is not a completed public launch: production hosting, provider credentials, sender approvals, legal terms, commercial prices and real-call acceptance remain activation dependencies. Extensions still outstanding are recorded explicitly.

## Architecture and authority

Signed Twilio webhooks select a tenant and approved channel. The database deduplicates provider events, stores bounded conversation context, and queues model work. Approved knowledge and verified preferences inform a schema-constrained intent. Server code validates every proposed operation, product, appointment and recipient. The model cannot execute arbitrary tools, SQL, URLs or code.

A messaging customer confirms an exact preview with `CONFIRM <action UUID>` within fifteen minutes. Streaming voice previews the same operation and requires the exact phrase “confirm this action” within two minutes. Interruptions revoke unconfirmed voice proposals. Owner pause, human takeover, approval, billing, opt-out and usage limits are rechecked. Provider-submitted requests cannot be recalled. Unknown action outcomes enter review instead of automatic retry. Human reconciliation records evidence without silently repeating the operation or telling a customer it succeeded.

Caller ID grants no private access. Voice conversations use a separate call-specific identity until the caller proves possession of a messaging channel. New voice bookings require that verification. A signed voice setup token binds the conversation to one tenant and call, not to an asserted telephone number.

## Delivery scope

| Requirement | Implemented | Activation or further extension |
|---|---|---|
| Natural voice | Signed ConversationRelay, FAQ model token streaming, interruptible speech, call budgets, zero transfer, call-scoped action previews and receipts | Twilio onboarding and real latency/interruption/load acceptance; autonomous structured decisions wait for the bounded decision before speaking |
| Automatic channel replies | SMS, approved WhatsApp/RCS routing, isolated threads, opt-out, WhatsApp 24-hour enforcement | Bind actual approved senders; approved WhatsApp templates and rich media remain extensions |
| Appointments | Native/Google availability, booking, cancellation, rescheduling, explicit confirmation, verified cross-channel ownership, one consented SMS reminder | Authorised Google credentials; self-service OAuth, Microsoft Calendar, holidays and leave remain extensions |
| Maintained knowledge | Owner sources, draft-only UTF-8 text/HTML snapshot imports, approval, expiry, relevance ranking, immutable revisions | Review source content; PDF parsing, automatic website crawling/refresh and semantic search remain extensions |
| Customer memory | Single-use channel proof, encrypted shared preference, verified appointment access, UNLINK and FORGET MEMORY | No biometric or caller-ID verification; organisation-specific sensitive-data workflows need stronger proof |
| Business actions | Internal lead inbox, exact-content requested SMTP email, signed durable integration events and scoped CRM polling API | Connect authorised CRM receiver/SMTP; dedicated external CRM adapters and tenant email OAuth remain extensions |
| Authority limits | Closed intents, owner catalogue and department controls, operation-specific confirmation, pause/takeover, daily request/message/call caps | Configure actual business policy and published allowances |
| Recovery | Durable actions, outboxes, booking IDs, Checkout idempotency, recorded review outcomes, no blind resend after unknown delivery | Provider reconciliation for interrupted actions; model/provider failover remains an extension |
| Operations and commercial analytics | Bookings/leads/actions, payment ledger, delivery status, existing usage counters, idempotent invoice-backed revenue/cost ledger and recorded margin | Import actual costs; the ledger is not certified total profit; conversion attribution and automatic metered invoicing remain extensions |
| Continuous quality | Owner cases, expected/forbidden terms, latency, opt-in daily runs, quota reservation, automatic pause after two failed runs | Choose representative cases; lexical tests do not certify factual accuracy or safety |
| Payments | Tenant-owned merchant integration, approved fixed GBP catalogue, customer-requested hosted Checkout, signed/retrieved paid-state receipts | Authorised merchant restricted key and webhook, approved tax-inclusive price IDs; voice payment links remain messaging-only |
| Security and privacy | Tenant isolation, encrypted provider/voice/action/identity secrets, MFA, CSRF, scoped expiring API keys, content retention, expanded owner export | Publish retention/terms; formal customer privacy-case handling and comprehensive account deletion remain extensions |
| Customer API and hooks | Read scopes, hashed 90-day keys, revocation, cursor event feed, signed action events, public-IP-pinned TLS webhook delivery | Connect receiver; verify signature freshness and durable event deduplication |
| Onboarding and launch | Account recovery, verification, tenant approval, number registration, subscription reconciliation, preflight and health checks | Dedicated Communications host/domain, live providers, final legal/commercial identity and live acceptance |

## Appointment and reminder behaviour

Native calendars coordinate this application's bookings. Slots enforce timezone, hours, weekdays, duration, a fourteen-day horizon and one-hour notice. Tenant and department locks protect local reservations. Google free/busy checks exclude external events; stable event IDs reconcile creation. Free/busy plus event insert is not an atomic provider reservation: other calendar clients can still race.

`REMIND <booking UUID>` on the booking's SMS channel authorises one reminder 24 hours before the appointment, only for appointments more than 24 hours away. Repeated consent does not repeat delivery. UK quiet hours are 20:00–09:00. Cancellation, rescheduling, stale appointments, opt-out, pause, takeover, billing and message limits block reminders. A rescheduled appointment requires fresh consent. No marketing permission is inferred. Queue receipts do not claim provider delivery.

## Verified memory

Send `LINK` from an original messaging channel. The code is encrypted in the outbox and redacted in stored message text. On another channel send `LINK <eight digits>`, or on a streaming call say “verify code” followed by the digits. Challenges expire after ten minutes, are single use and have tenant-level issuance/redemption attempt limits. Linking proves possession of a channel, not a legal identity. Existing separately verified identities require human review rather than automatic merging.

`REMEMBER <preference>` saves at most 150 characters for linked channels. Preferences are untrusted data, never permission to act. `FORGET MEMORY` clears the shared preference; `UNLINK` removes the current channel's association. Neither command deletes financial records or all message history. Verification responses are redacted before model context is constructed.

## Secure activation

1. Apply migrations through **0013**, restart the API/worker, and run `python -m app.manage check-config`. Model endpoint/key, encryption key and platform providers belong in the protected server configuration, never repository files or chat.
2. Configure the actual model and approved business facts. Enable the profile only for an approved, currently paid tenant. Autonomous and streaming voice are independent owner/operator controls.
3. Connect an authorised Google department calendar with `python -m app.manage connect-google-calendar <department-id> <calendar-id>`. Private prompts request OAuth client credentials and an already authorised refresh token. The command verifies free/busy, encrypts credentials and binds one tenant. It does not manufacture a consent grant.
4. Complete Twilio ConversationRelay onboarding, expose HTTPS/WSS, test signed setup/prompt/interrupt/end events and set `VOICE_STREAMING_ENABLED=true`. Calls disclose AI and offer zero transfer. Without a forwarding destination, the safe fallback ends the call.
5. After checking approval and subaccount ownership, bind channels using `python -m app.manage bind-sender <number-id> whatsapp whatsapp:+44... --approval-reference <reference>` or `... rcs rcs:<agent> --service-sid MG... --approval-reference <reference>`. These commands record bindings; they do not register or approve senders.
6. Use `python -m app.manage connect-stripe-merchant <tenant-id> <account-id>` for an authorised tenant merchant account. Private prompts accept its restricted API key and webhook signing secret. Configure the returned merchant endpoint for Checkout completion, asynchronous success/failure and expiry. Owner catalogue approval verifies a fixed GBP price and requires confirmation that published amounts include applicable taxes and charges. The AI never chooses arbitrary prices, discounts, subscriptions or card charges. The platform's subscription billing remains separate from tenant customer payments.
7. Connect a business receiver with `python -m app.manage connect-webhook <tenant-id> <https-url> <public-ip>`. TLS verifies the URL hostname while TCP connects only to the pinned public IP, preventing DNS rebinding/private-network access. IP rotation requires operator reconfiguration. A secret of at least 32 characters is prompted privately. Only set `--receiver-idempotent` after confirming receiver-side durable event deduplication.
8. Configure SMTP and the platform's verified sender. Customer confirmation previews exact recipient, subject and body. Queueing is not delivery; uncertain SMTP results remain reviewable.
9. Publish the selected retention policy before onboarding. Defaults scrub message/draft/lead content and completed action payloads after 90 days and voice history after 30 days. Accounting, consent, suppression and reconciliation metadata remain. Backup copies require their own expiry/deletion policy.
10. Run real SMS, WhatsApp/RCS where approved, voice interruption/transfer, verified booking/rescheduling, opt-out/reminder and merchant test-payment acceptance. AI-plan public checkout remains gated pending approved pricing and acceptance. Do not claim public launch based on code tests alone.

## Signed integration events

The receiver verifies `X-Raeburn-Signature = sha256=<hex HMAC-SHA256>` using its configured secret over the exact bytes `timestamp + "." + event_id + "." + request_body`. Check the timestamp is recent and store `X-Raeburn-Event-ID` durably before processing a repeat. The application does not follow redirects or store response bodies. A 2xx response means acknowledged, not that the CRM business action succeeded. Known 429/503 responses have at most three bounded attempts only when receiver idempotency is explicitly configured. Unknown delivery goes to review; a process crash after submission stays in sending for reconciliation.

The read API uses `Authorization: Bearer <issued key>`. Endpoints are `/api/customer/v1/leads`, `/bookings` and `/events`. Events return a `next_cursor`; supply it as `after` on the next request. Scope and tenant checks apply independently of browser sessions. No credential is returned again after creation.

## Operational limitations

- Streaming FAQ output begins before the full answer is available; evaluate the chosen model before live enablement. Structured autonomous decisions are validated before speech.
- Daily evaluation is a regression aid, not a factual or safety certification. It never edits business facts or grants new action authority.
- Model generations interrupted by a crash remain conservative/stuck instead of silently making another paid request. Daily checks interrupted while running also require operator reconciliation.
- All messaging channels consume the existing conservative shared allowance. Actual per-channel pricing and provider costs are not inferred from SMS segment counts.
- The owner export now includes decrypted voice/action/preference data and knowledge revisions, but is bounded and is not a complete statutory privacy-case workflow.
- Merchant payment receipts reconcile original payment, not later disputes/refunds or product fulfilment. Review those in the merchant account; automated refund/dispute handling remains an extension.
- Platform SMTP recipients are confirmed but are not verified email identities. Keep sensitive-record email workflows disabled until stronger identity verification exists.

## Primary references

- Twilio ConversationRelay: https://www.twilio.com/docs/voice/conversationrelay/websocket-messages
- Twilio WhatsApp window: https://www.twilio.com/docs/whatsapp/api
- Twilio RCS routing: https://www.twilio.com/docs/rcs/send-an-rcs-message
- Google event IDs: https://developers.google.com/workspace/calendar/api/guides/create-events
- Stripe hosted Checkout: https://docs.stripe.com/api/checkout/sessions/create
