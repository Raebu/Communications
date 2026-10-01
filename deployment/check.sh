#!/usr/bin/env bash
set -euo pipefail
: "${PUBLIC_URL:?Set PUBLIC_URL}"
curl --fail --silent --show-error "$PUBLIC_URL/health"
curl --fail --silent --show-error "$PUBLIC_URL/ready"
