# Raeburn Connect hosting

Approved public hostname: **connect.theraeburngroup.com**.

## Launch topology

Run the existing Docker Compose stack on a dedicated DigitalOcean host in London. Caddy serves HTTPS and forwards HTTP and WebSocket traffic to FastAPI. The portal, API and provider callbacks share one origin. The separate continuous worker processes durable jobs; PostgreSQL stores application state.

This is the lowest-change deployment for the implemented service. It still requires a separately provisioned host, secure operator access, private runtime configuration, backup/restore acceptance and real provider acceptance tests. A single host is not highly available; a commercial availability commitment must reflect this until redundant infrastructure is validated.

## Vercel assessment — 2 October 2026

The Vercel connection is available. No Communications project was identified in the inspected team. Do not repurpose the existing messenger or recruitment projects without establishing their ownership and repository relationship.

Vercel now supports WebSockets in beta, including Python integration examples. However, connections close at the function maximum duration. The current application also uses a continuous Python worker and Docker Compose services. A Vercel deployment would require validating the exact ASGI/Twilio protocol, duration/disconnection behaviour and replacing the worker deployment with an equivalent durable scheduling/execution design. Deploying the HTTP application alone would leave autonomous jobs unprocessed.

Keep the whole service on the dedicated host at launch. A separate static marketing site can use Vercel later without changing provider callback routing or authenticated portal origin.

References: [Vercel WebSockets](https://vercel.com/docs/functions/websockets), [function limits](https://vercel.com/docs/functions/limitations).

## DNS and activation

At the authoritative DNS provider for `theraeburngroup.com`, create an `A` record named `connect` pointing to the dedicated Communications host's public IPv4 address. Add an `AAAA` record only if IPv6 routing is configured and tested. Do not point this hostname at the existing recruitment staging hosts. No IP address has yet been allocated for this service.

Allow public TCP 80 and 443 for Caddy; restrict SSH to authorised operator access. Keep PostgreSQL and the API container ports private. Test DNS and HTTPS before registering the signed provider callbacks. Initially use direct DNS routing so the configured public URLs and signature verification can be tested without an additional proxy.

On the authorised host, clone `Raebu/Communications`, copy `deployment/production.env.example` to the repository root as `.env`, set its permissions to `600`, and configure the blank values privately. `POSTGRES_PASSWORD` and the URL-encoded password in `DATABASE_URL` must agree. The template keeps registration, sales and streaming voice disabled until their acceptance requirements are met.

Run `deployment/deploy.sh`, then run `python -m app.launch` inside the API container and `deployment/check.sh` with the approved public URL. Passing configuration checks is not a substitute for the real-call, messaging, payment and backup-restore tests in [LAUNCH_STATUS.md](LAUNCH_STATUS.md).

No separate `api.connect.theraeburngroup.com` hostname is needed for this launch. Introduce it only alongside a tested routing, cookie, CSRF and provider callback migration.
