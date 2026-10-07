#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
  echo 'Configure the protected .env file and managed database before deployment.' >&2
  exit 1
fi
chmod 600 .env
SUPABASE_CA_CERT_PATH="${SUPABASE_CA_CERT_PATH:-/opt/raeburn/certs/supabase-ca.crt}"
export SUPABASE_CA_CERT_PATH
if [[ ! -r "$SUPABASE_CA_CERT_PATH" ]]; then
  echo "Supabase CA certificate is not readable at $SUPABASE_CA_CERT_PATH." >&2
  echo 'Set SUPABASE_CA_CERT_PATH to the authorised host certificate path before deployment.' >&2
  exit 1
fi
stack=(docker compose -f compose.managed-db.yaml)
"${stack[@]}" config >/dev/null
"${stack[@]}" build
"${stack[@]}" run --rm --no-deps api python -m app.manage check-config
"${stack[@]}" run --rm --no-deps migrate
"${stack[@]}" up -d api worker proxy
"${stack[@]}" ps
