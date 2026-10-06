# Raeburn Connect email

Inbound routing is managed in Google Workspace. The operator confirmed the project addresses are configured; delivery has not yet been tested here.

| Purpose | Address |
| --- | --- |
| Default application sender | newphoneline@theraeburngroup.com |
| Reply-to and customer support | support@theraeburngroup.com |
| Verification sender and internal company-review recipient | verification@theraeburngroup.com |
| Billing contact displayed in account settings | billing@theraeburngroup.com |

Create a Resend sending-only API key restricted to theraeburngroup.com, then on the production server run:

```bash
cd /opt/raeburn/Communications
git pull --ff-only origin main
python3 deployment/configure-email.py
docker compose -f compose.managed-db.yaml -f compose.tls.yaml up -d --build api worker proxy
```

Paste the API key only at the hidden prompt. The script preserves other settings and atomically replaces .env with permissions 600. Never paste .env or the key into chat.

SMTP uses smtp.resend.com, port 465, username resend, and the API key as password. Reference: https://resend.com/changelog/smtp-service.

Changed company profile submissions queue encrypted customer acknowledgements and internal review alerts in the same transaction. Identical submissions do not queue more mail. Internal alerts include a company reference and dashboard link, without a registered address or documents. Configure email before customer onboarding: earlier submissions are not retrospectively emailed.

Account verification and password recovery use the verification sender. Existing AI email actions retain the default sender and support reply-to. Billing contact is a help link; Stripe invoice/receipt sender settings are managed separately in Stripe. The application does not claim email delivery until an actual provider/inbox test has passed. Failed SMTP jobs move to review rather than being automatically resent after an uncertain delivery.
