# Production server handoff — 6 October 2026

## Provisioned resources

- DigitalOcean: `raeburn-connect-production`, droplet `606614970`, London `lon1`, Ubuntu 24.04, 2 vCPU / 4 GB RAM / 80 GB disk.
- Public IPv4: `178.128.170.162`.
- Approved cost: $24/month billed hourly. The active server accrues charges even before application deployment.
- Supabase: `yptzmjcevwseddpdwdvb`, London; existing private schema `communications`, revision `0014`, login/owner `communications_app`.
- Intended domain: `connect.theraeburngroup.com`. DNS is not verified or changed by this handoff.

## Deployment access blocker

SSH from the current execution environment fails before authentication with `Network is unreachable`. No Docker installation, repository checkout, runtime credential installation or application start has occurred. An SSH public key named `raeburn-connect-deploy` (DigitalOcean key ID `59877490`) was installed at creation; its private key must never enter the repository or chat. Configure an operator-controlled SSH key through the DigitalOcean recovery/console workflow so access does not depend on a transient execution workspace.

## Operator activation sequence

1. Open this droplet's DigitalOcean console or use an authorised SSH client with a reachable network. Confirm the correct droplet ID and install an operator-controlled SSH key.
2. Install Docker Engine and the Compose plugin using Docker's official Ubuntu instructions. Verify `docker compose version`. Restrict inbound access to SSH from operator addresses and public TCP 80/443; permit required outbound HTTPS/database traffic.
3. Check out `https://github.com/Raebu/Communications` on the host using authorised repository access. Never put a GitHub access token in a shell command or repository URL.
4. Copy `deployment/supabase.env.example` to root `.env`, set mode `600`, and enter credentials directly on the host. Use the existing Supabase login with its exact Connect-panel endpoint and verified TLS as described in [SUPABASE.md](SUPABASE.md). Preserve a secure backup of application encryption keys. Do not reset an existing database password merely to retrieve it.
5. Configure the production AI, Twilio, Stripe, SMTP and legal/service settings. Keep public sales and registration disabled until the launch checks and acceptance tests pass.
6. Set the approved domain's DNS A record to `178.128.170.162`. Check for a conflicting AAAA record before issuing certificates; do not publish an IPv6 address the host does not have.
7. Run `bash deployment/deploy-managed-db.sh`, then `docker compose -f compose.managed-db.yaml exec -T api python -m app.launch`. Resolve failed checks before opening sales.
8. Verify HTTPS, worker readiness and real voice/message/booking/payment flows using authorised test recipients and transactions. Configure database backups and prove an isolated restore before commercial launch.

The application is not live until the host, provider configuration and acceptance checks are verified. Provisioning success alone is not launch acceptance.
