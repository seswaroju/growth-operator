#!/usr/bin/env bash
# Database backup (security-hardening S3, audit #16e).
#
# Writes a compressed, portable pg_dump (custom format) into ./backups, timestamped. The dump
# CONTAINS REAL DATA — ./backups is gitignored and must never be committed. Restore is verified by
# scripts/db_restore_drill.sh (run continuously in CI); restore into a DB with scripts/db_restore.sh.
#
# Connection: GROWTH_OPERATOR_DATABASE_MIGRATOR_URL (owner role), else the local dev default.
# Needs pg_dump on PATH (postgresql-client). For real backups, run on the DB host / server.
set -euo pipefail

RAW="${GROWTH_OPERATOR_DATABASE_MIGRATOR_URL:-postgresql://growth_operator:growth_operator@localhost:5432/growth_operator}"
URI="${RAW/+asyncpg/}"
URI="${URI%%\?*}"
DBNAME="${URI##*/}"

OUT_DIR="${BACKUP_DIR:-backups}"
mkdir -p "$OUT_DIR"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="$OUT_DIR/${DBNAME}-${TS}.dump"

pg_dump --format=custom --no-owner --no-privileges --dbname="$URI" --file="$OUT"
echo "backup written: $OUT ($(wc -c <"$OUT" | tr -d ' ') bytes)"
echo "verify a backup restores with: scripts/db_restore_drill.sh   (or: make backup-drill)"
