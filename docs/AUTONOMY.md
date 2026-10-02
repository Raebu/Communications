# Autonomous service delivery status — 0.4

This is a tested code increment, not a completed public launch. Earlier 0.3 documentation describes the initial draft-only implementation. The additions below supersede that limitation when an owner enables autonomous messaging.

## How autonomy works

Signed inbound webhooks map the sender to a tenant and channel. Duplicate provider message identities are ignored. An active, approved tenant with enabled autonomy creates one durable generation job. The model proposes a schema-constrained intent. Server code validates the selected operation, recipient, calendar and slot. It never executes arbitrary model tools, SQL, URLs or code.

Answers queue through existing consent, local allowance, approved sender, billing and provider spend controls. A human takeover or pause stops queued AI messages before provider submission. Requests already submitted to a provider cannot be recalled. Explicit HUMAN/AGENT messages switch ownership to staff. Model uncertainty/invalid structured output sends a short handoff acknowledgement and stops subsequent automation for the thread.

Bookings, cancellation, rescheduling, requested emails and lead capture produce a durable action proposal. The customer sees the operation and must reply `CONFIRM <action UUID>` within fifteen minutes. Confirmation is scoped to the same tenant and conversation. The action worker rechecks autonomy, approval, billing and ownership. Receipts only report success after the local operation commits. SMTP receipts report **queued**, never falsely claim delivered.

Department calendars are native to this application. Tenant locking and department locking prevent two application bookings claiming the same slot. Times persist in UTC and display in the department timezone. Slots enforce business hours, weekdays, duration, fourteen-day horizon and one-hour notice. A department can additionally connect to Google Calendar through the tenant integration adapter. Free/busy checks exclude external bookings; Google events use stable request IDs for reconciliation. Microsoft Calendar, holidays and staff leave rules remain outstanding.

## Expanded scope: honest status

| Requirement | Implemented | Remaining |
|---|---|---|
| Natural voice | Signed ConversationRelay transport, model token streaming, interruption cancellation, zero transfer, bounded call/session, encrypted history | Live Twilio onboarding, voice latency/load and interruption acceptance; business actions over voice |
| Autonomous channels | SMS and approved WhatsApp/RCS routing, automatic replies, isolated threads, opt-out, WhatsApp 24-hour window | Actual sender approvals/bindings, templates/rich media, channel-specific pricing |
| Appointment management | Internal and authorised Google department calendars, booking, customer-confirmed cancellation/rescheduling, conflict prevention, receipts | Self-service Google OAuth, Microsoft Calendar, holidays/leave, reminders |
| Knowledge | Approved owner-entered sources, expiry checks, relevance ranking, source IDs/versions | Safe website/PDF ingestion, refresh jobs, immutable version history, stronger retrieval/citations |
| Customer memory | Bounded same-thread context and encrypted voice session history | Verified cross-channel identity, preference memory, privacy retention/deletion flows |
| Business actions | Internal leads/CRM inbox, requested platform-SMTP outbox, durable receipts | External CRM connectors, tenant-owned email OAuth/domains, support-ticket integrations |
| Authority | Closed intent schema, approved slots, exact-operation confirmation, ownership and account checks | Configurable per-action policies, refunds/discounts, verified hosted payment workflows |
| Reliability | Durable DB jobs, idempotency, pause, conservative ambiguous-send handling, model-failure handoff | Provider failover, integration reconciliation UI, outage recovery/load tests |
| Dashboard | Conversations, handoff ownership, bookings, leads, actions, failure counts | Actual cost/margin analytics, conversion attribution, billing meters |
| Quality testing | Regression evaluation API, automated contract/security/replay/channel/booking tests | Real-model and real-call evaluations, rollout controls, continuous production quality pipeline |
| Self-service onboarding | Business/profile and knowledge/calendar setup forms | OAuth connection wizard, end-to-end activation test and launch checks |
| Departments | Separate named booking schedules and descriptions | Department-specific model instructions, routing and forwarding destinations |
| Follow-up | No unsolicited or scheduled autonomous follow-up enabled | Consent ledger, reminders, quiet hours and frequency caps |
| Multilingual | Configurable speech language and model-language instruction | Certified supported language pairs and business knowledge translation |
| Payments | Existing Stripe hosted service subscription checkout | Customer payment-link action, payment receipts, refund authority |
| Commercial controls | Daily AI request limit, shared conservative messaging cap, call reservations/provider budget | Usage billing, monetary model ceilings, margin alerts |
| Security/privacy | Tenant scoping, encrypted voice/action payloads, owner controls, export API, existing MFA | Automated retention, authenticated customer privacy requests, complete data deletion/export |
| Abuse protection | Signed ingress, limits, closed tools, UK-only outgoing messaging, no blind ambiguous retries | Robust model abuse evaluations, content filtering, anomaly alerting |
| Human takeover | Thread AI/human/paused ownership, staff replies take ownership, explicit return to AI | Agent presence, assignment queues, CRM context handoff |
| APIs/webhooks | Authenticated application API and signed provider ingress | Customer API keys, signed outbound event subscriptions, connector health monitoring |

The table is intentionally explicit: code for one item does not mean every expanded capability or commercial launch requirement is complete.

## Activation

1. Migrate through 0006, restart API and worker. Configure the existing model endpoint and secrets. No model is installed or deployed by these changes.
2. In **AI receptionist**, enter accurate business facts; enable SMS drafts and **Send AI replies automatically**. The global pause switch stops new automated replies/actions. Use inbox controls to transfer a thread to human ownership or resume AI.
3. Add approved knowledge documents and department calendars. For Google Calendar, run `python -m app.manage connect-google-calendar <department-id> <calendar-id>` in the secure deployment terminal. It prompts privately for OAuth client ID/secret and an already-authorised refresh token, verifies availability access, and stores the configuration encrypted for that tenant. Use scopes sufficient for event writes and free/busy reads. The consent grant is not manufactured by this command; self-service OAuth onboarding is still required.
4. Configure SMTP with the platform's verified sender. Requested email content/recipient is previewed for customer confirmation. Outbox ambiguity remains operator-reviewable; no blind resend after an unknown SMTP result.
5. For streaming voice, complete Twilio ConversationRelay onboarding, expose the existing app over HTTPS/WSS, verify signed setup/prompt/interrupt/end events, then set `VOICE_STREAMING_ENABLED=true`. Streaming currently answers business questions; action execution remains messaging-only.
6. For WhatsApp/RCS, an operator must first verify approval and sender ownership in that tenant's Twilio subaccount, then run `python -m app.manage bind-sender <number-id> whatsapp whatsapp:+44... --approval-reference <provider-reference>` or `... rcs rcs:<agent> --service-sid MG... --approval-reference <provider-reference>`. Configure signed inbound/status callbacks in the provider service. This command records the approval reference; it does not register or approve the sender. No cross-tenant shared sender pool is allowed.
7. Test a real customer message/call and action completion before public marketing. AI-plan public checkout remains gated; do not invent prices or change live subscriptions before costs/terms are agreed.

## Operational limitations

- Native calendars coordinate this application's bookings only. Connected Google calendars additionally exclude verified free/busy intervals. Google free/busy plus insert is not an atomic provider reservation: other calendar clients can still race. If an external operation has an unknown result, its action is left in review rather than blindly retried.
- A phone number is not proof of identity. No private account history is exposed through cross-channel model memory.
- Approved-source prompting is not a formal hallucination prevention guarantee. The model can still answer incorrectly; evaluate the chosen model.
- Streaming starts speaking before the full reply is available; model safety must be evaluated before enabling it publicly. Output is bounded but is not comprehensively content-filtered.
- Generating jobs interrupted by a crash remain conservative/stuck rather than silently making another billable generation request. Inspect provider usage before manual reconciliation.
- All channels currently consume the existing conservative messaging allowance; financial per-channel pricing is not inferred from SMS segment counts.
- The export API is owner-only and currently excludes decrypted voice transcripts/action payloads; it is an operational export, not yet a complete privacy-request system.
- SMTP recipients are customer-confirmed but not verified email identities. Do not enable sensitive-record email workflows until verification exists.

## Primary implementation references

- Twilio ConversationRelay WebSocket protocol: https://www.twilio.com/docs/voice/conversationrelay/websocket-messages
- Twilio ConversationRelay noun: https://www.twilio.com/docs/voice/twiml/connect/conversationrelay
- Twilio WhatsApp service window: https://www.twilio.com/docs/whatsapp/api
- Twilio RCS messaging: https://www.twilio.com/docs/rcs/send-an-rcs-message
