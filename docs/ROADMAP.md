# Delivery roadmap

## Implemented and locally tested

- Customer registration (closed by default), password authentication, session expiry and CSRF controls.
- Business legal profile and operator review portal.
- Per-customer Twilio subaccount/key provisioning and encrypted credentials.
- GB live inventory search with digit matching; approved bundle/type/address checks.
- One number per paid subscription; durable purchase jobs with provider reconciliation.
- Fixed Stripe subscription Checkout/Portal and signed current-state webhook processing.
- Threaded SMS inbox, consent acknowledgement, opt-out suppression, queued replies and delivery callbacks.
- UK call forwarding with account checks and provider-budget check.
- Tenant-scoped access, audit records, rate limits, local segment cap.
- Docker/PostgreSQL deployment definition, CI and critical-flow tests.

## Before paid public launch

| Priority | Work | Acceptance |
| --- | --- | --- |
| Critical | Twilio credentials, real approved bundles and provider acceptance tests | Genuine test customer can activate a number and complete inbound/outbound SMS and forwarding |
| Critical | Live Stripe prices, signed webhook setup and real invoice/checkout acceptance | No unpaid/duplicate subscriptions activate service; refund/cancellation failures recover |
| Critical | UK communications-provider/contract/privacy review | Applicable obligations assessed; final terms/privacy/complaints/emergency limitations published |
| Critical | Fraud controls and budget reservations | Concurrent calls/messages cannot bypass agreed financial limits; alerts and suspensions tested |
| Critical | Deploy and validate persistent hosting, encrypted backup restore, migrations and redacted logs | Restore drill succeeds; worker/webhook/payment drift alerts arrive |
| Critical | Validate deployed operator MFA, email verification and recovery | Privileged actions require strong access; customer email verified; safe recovery path |
| High | Usage ledger and usage billing | Provider records reconcile to customer invoices with tax/refund rules |
| High | Operator reconciliation UI | Review messages/orders safely reconciled without manual SQL |
| High | Porting, number release, customer deletion and export | Documented customer lifecycle works end-to-end |
| High | PostgreSQL concurrency and failure-injection acceptance | Multiple workers/webhooks do not double-provision or bypass caps |
| High | Frontend accessibility and browser E2E suite | Keyboard/mobile workflows and reload/timeout errors pass |
| High | Consent evidence and provider opt-out integration | Opt-in evidence retained; every channel and queued action honours revocation |

## Expansion

1. Team memberships, invitations, agent roles, per-thread assignments, persisted conversations/pagination, attachment handling and call history.
2. Per-tenant AI receptionist with explicit handoff, knowledge sources, action scopes and model-cost ceilings.
3. OAuth calendar connections and deterministic booking transactions; email integrations with verified customer senders.
4. WhatsApp onboarding/template/session enforcement; RCS agent verification/capability checks and channel callback handlers.
5. Additional-number billing, US A2P/toll-free registration, international destination policies and per-country requirements.
6. Voice AI, voicemail, optional recordings/consent, WebRTC and SIP only after the underlying voice product is operationally ready.

No speculative dates or completion percentages are assigned to unimplemented features. Existing Raeburn production communications integrations need a separately reviewed migration; none were available in this empty repository.

## Commercial implementation update — 0.2.0

Email verification/recovery, authenticator MFA, versioned migrations, periodic invoice reconciliation, worker heartbeat, local monthly usage allowances, call reservations/completion ledger, HTTPS and encrypted backups have been implemented. The remaining launch boundary is secure live provider configuration, deployed host/DNS, final service identity/policies/prices, and real acceptance tests. See [LAUNCH.md](LAUNCH.md). Usage overage billing and AI/channel expansion remain unimplemented.
