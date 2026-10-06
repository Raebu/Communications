# Automated company and domain verification

The automated route supports active UK limited companies with an individual current director whose full name and birth month/year match a live Stripe Identity document-and-selfie result. It does not treat a domain, mailbox, company number or applicant declaration as company authority on its own. Employees, overseas entities, sole traders, shared hosting and internationalised domains remain outside automatic approval.

## Customer journey

1. Submit the exact registered company name, eight-character company number and full registered office address. Address formatting can differ, but words, postcode and numbers must match the register.
2. Enter the registered domain and an email on that exact domain, existing contact mobile and number type. Confirm authority. Production owners must enable authenticator MFA.
3. Receive an account-bound, single-use email link valid for 30 minutes. It needs an explicit confirmation after sign-in; mail scanners cannot confirm a GET link.
4. Add the per-application TXT record `_raeburn-connect.<domain>`. The worker checks it automatically. Keep it in place for scheduled checks.
5. After mailbox, DNS and registry checks pass, use Stripe's hosted document/selfie check. Documents and birth dates are not stored by this application. Only names needed for telephone registration are retained encrypted.
6. The worker retrieves Stripe results directly and matches the exact identity to one current Companies House director. Browser redirects and submitted statuses never approve anything.
7. If automatic Twilio processing is enabled, the worker creates a customer subaccount and reconciles address, business end-user, structured supporting document and bundle resources. It checks the contact mobile with Twilio Lookup line-type intelligence, queries current regulation requirements, evaluates and submits the bundle, then polls provider approval. Unknown documents/requirements hold activation; the app never fabricates documents.
8. On Twilio approval, the customer can subscribe and request a number. This verification flow does not purchase numbers or charge a subscription. Identity verification can incur Stripe fees, and Twilio Lookup line-type intelligence can incur lookup fees. Mobile classification does not independently establish that the applicant owns the contact number; director authority is established through the separate identity check.

Customers receive instructions, state updates and at most two reminders after one and two days. Unfinished attempts expire after seven days. Verified applications are checked daily against domain control, the register, current director authority and provider approval. Failed checks hold activation. The hosted identity check can be reopened to correct input without automatically creating additional paid sessions.

## Domain safeguards

- Exact normalized business email/domain match; no suffix or substring acceptance.
- Reject paths, URLs, ports, credentials, subdomains, trailing dots, IP literals, invisible characters, Unicode and punycode from automatic activation.
- Use a bundled public suffix list including private suffixes; reject public suffixes, shared hosting and known consumer email domains.
- Protect configured domains and already verified customer domains using cross-TLD brand comparisons, close edit distances, hyphen removal and common ASCII visual substitutions (`rn/m`, `vv/w`, digits/letters).
- `TRUSTED_COMPANY_DOMAINS` can pin an independently established official company-domain association. The applicant cannot change that binding. Exact platform-domain exceptions need an explicit binding to the correct company number.
- Exclusively claim a company number and domain using database constraints. An unfinished malicious application cannot reserve a protected domain indefinitely.

Lookalike detection is a conservative heuristic against the configured/verified domain set, not a global trademark database or proof of an official website. Companies House does not supply an authoritative company-domain directory. DNS proof plus verified director authority establishes a director-authorised domain claim; independently pinned domains provide a stronger prior association. Legitimate close brands may be held. Never claim universal impersonation detection.

Company profile changes invalidate old proofs and invalidate activation. Account and domain tokens are tenant-bound; stale links cannot approve a changed identity. Production checkout, administrator approval and number provisioning require current company proof. Unrelated Twilio bundle IDs cannot bypass the automated evidence chain. No remote user website is fetched. All HTTP calls go to fixed official provider hosts.

## One-time operator configuration

1. Get a read-only Companies House public data API key: https://developer.company-information.service.gov.uk/.
2. Enable Stripe Identity and review its eligibility, verification fees and privacy obligations in your Stripe account. Create a restricted live key permitting Identity sessions (create/read/redact) and verification reports (read). Grant Read access to Identity Verification Results, Recent Detailed Verification Results and All Detailed Verification Results. Restrict this key to the production server IP (currently 178.128.170.162); access to older results needs this IP restriction. The server explicitly expands verified_outputs.dob to compare birth month/year with the director register without storing the date of birth. Do not reuse a browser key or an unrestricted secret key. No live Identity session has been created by this change.
3. Configure Resend sending credentials using `deployment/configure-email.py` if not already done. Existing smtp.resend.com settings use the HTTPS API automatically because DigitalOcean blocks SMTP ports; the saved key and sender aliases remain valid.
4. Run `python3 deployment/configure-verification.py` in the production repository to privately store `COMPANIES_HOUSE_API_KEY` and `STRIPE_IDENTITY_KEY`. The script keeps activation disabled. Set `COMPANY_VERIFICATION_ENABLED=true` only after registry and Identity acceptance tests. Authentication keys must remain in the server's mode-600 `.env`.
5. Set `VERIFICATION_AUTO_TWILIO=true` after Main-key/subaccount configuration and a real GB regulatory acceptance test. This is separate from the Identity flag. The UK requirement schema is queried live; supported structured business-address proof is built only from the matched register address.
6. Review `PROTECTED_DOMAINS`; optionally configure trusted company number/domain JSON bindings from independently checked evidence. For your own platform domain, pin the exact company that legally operates it, not a similarly named subsidiary.
7. Set `VERIFICATION_DAILY_LIMIT` to an approved Identity spending limit (default 20 sessions/day, maximum 100). It is a count cap, not a currency budget. The application also caps start attempts to three per tenant/day and business-email challenges to five per attempt.
8. Apply migration 0015 and recreate the service using both managed DB and TLS compose files. Keep public sales closed until real provider, email and Twilio acceptance tests pass.

```bash
cd /opt/raeburn/Communications
git pull --ff-only origin main
docker compose -f compose.managed-db.yaml -f compose.tls.yaml build
docker compose -f compose.managed-db.yaml -f compose.tls.yaml run --rm --no-deps migrate
docker compose -f compose.managed-db.yaml -f compose.tls.yaml up -d api worker proxy
```

Schema 0015 creates private RLS-enabled verification and claim tables. The migration does not grandfather old company approvals into verified proofs. Existing production tenants need verification before a new checkout or number provisioning.

## Holds, retention and acceptance

Provider outages retry after ten minutes; uncertain remote mutations and unsupported evidence hold activation without blind retries. Remote resources use per-attempt names and read-after-write reconciliation. There is no operator auto-approval override. Customers can correct details, or restart a held attempt within the daily cap. Unresolvable cases remain on hold; zero routine administration does not guarantee every applicant can be approved.

Abandoned Identity checks are requested for redaction after 30 days; references remain until a successful request. Restarting an abandoned attempt requests redaction before discarding its old reference. Verified Identity evidence is retained at Stripe for ongoing authority checks: disclose the retention purpose, provide a deletion/offboarding process and configure Stripe's retention policy before live onboarding. This implementation does not claim a complete regulatory/KYC or privacy compliance certification.

Before launch, verify the entire route using your own legitimate company, verify provider failures/holds, confirm acknowledgement and review email delivery, and test a database restore. Live account configuration, identity verification and Twilio approval have not been executed from this workspace.

Primary implementation references:
- https://developer.company-information.service.gov.uk/authentication
- https://docs.stripe.com/api/identity/verification_sessions/create
- https://docs.stripe.com/api/identity/verification_reports/object
- https://www.twilio.com/docs/phone-numbers/regulatory/reading-regulations-for-the-uk-bundle
- skill://twilio-developer-kit@openai-curated-remote/root/.codex/plugins/cache/openai-curated-remote/twilio-developer-kit/0.2.2/skills/twilio-regulatory-compliance-bundles/SKILL.md
