#!/usr/bin/env bash
# Database restore (security-hardening S3, audit #16e).
#
#   scripts/db_restore.sh <dump-file> <target-db-name> [--force]
#
# Restores a pg_dump (custom format) into <target-db-name> on the same server as the connection URL.
# GUARDRAILS (this drops + recreates the target database):
#   * refuses a target whose name contains "prod" (never restore over production from this script);
#   * refuses to overwrite the PRIMARY database (the one in the URL) unless --force is given.
#
# Connection: GROWTH_OPERATOR_DATABASE_MIGRATOR_URL (owner role), else the local dev default.
set -euo pipefail

DUMP="${1:-}"
TARGET="${2:-}"
FORCE="${3:-}"
if [ -z "$DUMP" ] || [ -z "$TARGET" ]; then
  echo "usage: scripts/db_restore.sh <dump-file> <target-db-name> [--force]" >&2
  exit 2
fi
[ -f "$DUMP" ] || { echo "error: dump file not found: $DUMP" >&2; exit 2; }

RAW="${GROWTH_OPERATOR_DATABASE_MIGRATOR_URL:-postgresql://growth_operator:growth_operator@localhost:5432/growth_operator}"
URI="${RAW/+asyncpg/}"
URI="${URI%%\?*}"
PREFIX="${URI%/*}"
PRIMARY="${URI##*/}"
ADMIN_URI="$PREFIX/postgres"
TARGET_URI="$PREFIX/$TARGET"

case "$TARGET" in
  *prod*) echo "refusing: target '$TARGET' looks like production." >&2; exit 3 ;;
esac
if [ "$TARGET" = "$PRIMARY" ] && [ "$FORCE" != "--force" ]; then
  echo "refusing to overwrite the primary database '$PRIMARY' without --force." >&2
  echo "restore into a scratch DB instead, e.g.: scripts/db_restore.sh '$DUMP' ${PRIMARY}_restore" >&2
  exit 3
fi

echo "restoring '$DUMP' -> database '$TARGET' (drop + recreate)"
psql "$ADMIN_URI" -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$TARGET\" WITH (FORCE);" >/dev/null
psql "$ADMIN_URI" -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$TARGET\";" >/dev/null
pg_restore --no-owner --no-privileges --dbname="$TARGET_URI" "$DUMP"
echo "restore complete -> $TARGET"
