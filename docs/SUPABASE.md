# Supabase deployment preparation

The dedicated **Raeburn Connect** project `yptzmjcevwseddpdwdvb` is active in London (`eu-west-2`). The private `communications` schema is owned by the login `communications_app`; migrations through `0014` are applied. All 34 application tables have RLS enabled, and SQL privilege checks show no table SELECT grants to `anon` or `authenticated`. Runtime connectivity and Data API exposure still need verification.

## Connection and isolation

Supabase provides the database; the API, portal, signed voice WebSocket endpoint and continuous worker run on DigitalOcean using `compose.managed-db.yaml`. Keep the existing application authentication and tenant authorization. This deployment does not introduce Supabase Auth, browser database access or Supabase service-role keys.

Before migrations, create a dedicated database login and a private schema named `communications` owned by that login, using the project's authorised SQL administration interface. Give it only the privileges needed for this application's schema. Keep that schema outside the Data API's exposed schemas and revoke schema access from `PUBLIC`, `anon` and `authenticated`. Disable the project's Data API if it is unused. Do not migrate this application's sensitive tables into an exposed `public` schema.

Configure the login's `search_path` as `communications`, and verify it for both runtime and migration connections. If using a connection URL option instead, its URL-encoded query value is `options=-csearch_path%3Dcommunications`. PostgreSQL still implicitly searches `pg_catalog`. Do not include a schema writable by untrusted users in the path.

Use the direct connection when the host has working IPv6, or the **session-mode** pooler for IPv4. Copy the exact host, port and username from the project's Connect panel. Use the SQLAlchemy driver prefix `postgresql+psycopg://`; URL-encode credentials. Avoid the transaction-mode pooler for this initial deployment because its prepared-statement and session behaviour requires separate validation.

Require TLS with certificate and hostname verification (`sslmode=verify-full`), using the project's documented CA certificate where required. Mount any required CA certificate read-only into both API/worker and migration containers, and reference its container path with `sslrootcert`. Test the actual connection; do not fall back to disabling verification.

## Deployment

On the authorised DigitalOcean host, copy `deployment/supabase.env.example` to the root `.env`, protect it with mode `600`, and configure credentials privately. Then run:

```bash
bash deployment/deploy-managed-db.sh
docker compose -f compose.managed-db.yaml exec -T api python -m app.launch
```

This standalone Compose stack has no local PostgreSQL service and requires no `POSTGRES_PASSWORD`. Do not combine it with the local-database Compose stack. Use the same managed database connection for runtime and migrations; never place its password in repository files or chat.

## Acceptance before launch

Verify TLS, current schema and migration revision `0014`; inspect that all application tables and the Alembic version table are in `communications`. Test API and worker transactions, tenant separation, callbacks, voice interruption and confirmed actions against the actual managed database. Run Supabase security advisors and verify anonymous/authenticated Data API requests cannot access application tables.

For private-schema defense in depth, enable RLS on the application's tables after reviewing access roles. The schema-owning backend login bypasses ordinary RLS; tenant authorization remains enforced by the application. Do not add permissive browser policies or claim database-enforced tenant isolation from this arrangement. Any future direct-client access requires a separately designed identity mapping and tenant policies.

Select a production backup plan and prove a restore into an isolated environment. `deployment/backup.sh` targets the local Compose database and must **not** be used for this stack. Supabase backups and any separately configured encrypted logical exports must have an explicit retention and off-site recovery policy. Exported schema data alone does not preserve encryption keys needed to decrypt application content.

## Current account evidence — 6 October 2026

Supabase project and schema provisioning are verified. The dedicated DigitalOcean London server is active at `178.128.170.162`, but deployment access is blocked by this execution environment's network route. See [SERVER_HANDOFF.md](SERVER_HANDOFF.md). Stripe live account access does not supply the application's restricted key, webhook or subscription prices. Twilio and protected runtime credentials still require activation.

References: [database connections](https://supabase.com/docs/guides/database/connecting-to-postgres), [Data API security](https://supabase.com/docs/guides/api/securing-your-api), [backups](https://supabase.com/docs/guides/platform/backups).
