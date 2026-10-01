#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${BACKUP_RECIPIENT:?Set the age public recipient key for encrypted backups}"
command -v age >/dev/null
mkdir -p /var/backups/raeburn-communications
chmod 700 /var/backups/raeburn-communications
backup_file="/var/backups/raeburn-communications/$(date -u +%Y%m%dT%H%M%SZ).sql.age"
docker compose exec -T db pg_dump -U communications -d communications --no-owner | age -r "$BACKUP_RECIPIENT" > "$backup_file"
chmod 600 "$backup_file"
echo 'Encrypted database backup completed; replicate it to your off-site backup store.'
