#!/usr/bin/env bash
# Nightly pilot backup (PILOT-1A): dump -> encrypt -> off-site -> prune.
#
# Wraps the existing scripts/db_backup.sh rather than replacing it, so the thing CI already
# exercises (scripts/db_restore_drill.sh) stays the thing that runs in production.
#
# Two properties this adds that a bare pg_dump does not have:
#
#   * The off-site copy is ENCRYPTED before it leaves the host. A dump is every customer phone
#     number, every conversation and every price the store has ever quoted; object storage is
#     durable, not confidential, and a bucket that is public by accident is a routine incident.
#     Encrypting with the same age recipient as SOPS means the founder already holds the only key.
#
#   * The destination is S3-COMPATIBLE and supplied by environment. DigitalOcean Spaces, Backblaze
#     B2, S3 and MinIO all work unchanged; no provider is hard-coded, so moving is a config change.
#
# Retention: 7 daily on the host, and whatever lifecycle policy the founder sets on the bucket.
# Deliberately no PITR — that is a real cost in complexity and the pilot's RPO of one day is a
# decision, not an oversight.
#
#   crontab:  15 3 * * *  cd /opt/vaylorn && scripts/backup-nightly.sh >> /var/log/vaylorn-backup.log 2>&1
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/vaylorn}"
export BACKUP_DIR
mkdir -p "$BACKUP_DIR"

echo "[$(date -u +%FT%TZ)] starting nightly backup"

# 1. dump (the existing, drill-verified mechanism)
scripts/db_backup.sh
DUMP="$(ls -t "$BACKUP_DIR"/*.dump 2>/dev/null | head -1)"
[ -n "$DUMP" ] || { echo "FATAL: no dump produced" >&2; exit 1; }

# 2. encrypt to the founder's age recipient — the same public key that protects the secrets file,
#    so there is one key to keep safe rather than two.
RECIPIENT="${BACKUP_AGE_RECIPIENT:-}"
if [ -n "$RECIPIENT" ] && command -v age >/dev/null 2>&1; then
  age -r "$RECIPIENT" -o "${DUMP}.age" "$DUMP"
  shred -u "$DUMP" 2>/dev/null || rm -f "$DUMP"
  DUMP="${DUMP}.age"
  echo "encrypted: $DUMP"
else
  # Loud, not silent: an unencrypted dump leaving the host is a decision someone should make
  # knowingly. The backup still happens — a missing key must not mean no backup at all.
  echo "WARNING: BACKUP_AGE_RECIPIENT unset or age missing — dump is NOT encrypted" >&2
fi

# 3. off-site, if configured
if [ -n "${BACKUP_S3_BUCKET:-}" ] && command -v aws >/dev/null 2>&1; then
  aws s3 cp "$DUMP" "s3://${BACKUP_S3_BUCKET}/$(basename "$DUMP")" \
    ${BACKUP_S3_ENDPOINT:+--endpoint-url "$BACKUP_S3_ENDPOINT"}
  echo "uploaded to s3://${BACKUP_S3_BUCKET}/$(basename "$DUMP")"
else
  echo "NOTE: BACKUP_S3_BUCKET unset — backup is local only, so a lost droplet loses it too" >&2
fi

# 4. prune local copies
find "$BACKUP_DIR" -name '*.dump*' -mtime "+${KEEP_DAYS}" -delete
echo "[$(date -u +%FT%TZ)] done; local retention ${KEEP_DAYS}d"
