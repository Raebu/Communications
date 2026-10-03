#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
  echo 'Configure the protected .env file and managed database before deployment.' >&2
  exit 1
fi
chmod 600 .env
stack=(docker compose -f compose.managed-db.yaml)
"${stack[@]}" build
"${stack[@]}" run --rm --no-deps api python -m app.manage check-config
"${stack[@]}" run --rm --no-deps migrate
"${stack[@]}" up -d api worker proxy
"${stack[@]}" ps
