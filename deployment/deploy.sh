#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
  echo 'Create the protected .env file on the server before deployment.' >&2
  exit 1
fi
chmod 600 .env
# Preflight must use the real environment; never print or source provider keys.
docker compose -f compose.yaml -f compose.production.yaml build
docker compose -f compose.yaml -f compose.production.yaml run --rm --no-deps api python -m app.manage check-config
docker compose -f compose.yaml -f compose.production.yaml up -d db
docker compose -f compose.yaml -f compose.production.yaml run --rm migrate
docker compose -f compose.yaml -f compose.production.yaml up -d api worker proxy
docker compose -f compose.yaml -f compose.production.yaml ps
