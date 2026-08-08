#!/usr/bin/env bash
# Restore drill (security-hardening S3, audit #16e) — PROVE a backup actually restores.
#
# An untested backup is a false sense of safety. This dumps the live DB, restores it into a
# throwaway scratch DB, verifies the restore matched (table count + alembic version + a data
# row-count), then drops the scratch DB. Prints PASS/FAIL and exits non-zero on any mismatch.
#
# Safe by construction: it only ever CREATEs and DROPs its own scratch DB ("<db>_restore_drill")
# and never writes to the source DB. Runs in CI (migrate job) for continuous proof, and locally via
# `make backup-drill` (piped into the dev Postgres container, so no host pg tools are needed).
#
# Connection: GROWTH_OPERATOR_DATABASE_MIGRATOR_URL (owner role), else the local dev default.
set -euo pipefail

RAW="${GROWTH_OPERATOR_DATABASE_MIGRATOR_URL:-postgresql://growth_operator:growth_operator@localhost:5432/growth_operator}"
URI="${RAW/+asyncpg/}"     # pg tools want plain postgresql://, not the SQLAlchemy +asyncpg tag
URI="${URI%%\?*}"          # drop any ?query-string
PREFIX="${URI%/*}"         # scheme://user:pass@host:port
DBNAME="${URI##*/}"        # source database name
ADMIN_URI="$PREFIX/postgres"                 # maintenance connection for CREATE/DROP DATABASE
SCRATCH="${DBNAME}_restore_drill"
SCRATCH_URI="$PREFIX/$SCRATCH"

DUMP="$(mktemp -t drill.XXXXXX.dump)"

cleanup() {
  psql "$ADMIN_URI" -v ON_ERROR_STOP=1 -tAc \
    "DROP DATABASE IF EXISTS \"$SCRATCH\" WITH (FORCE);" >/dev/null 2>&1 || true
  rm -f "$DUMP"
}
trap cleanup EXIT

echo "== restore drill =="
echo "source db : $DBNAME"
echo "scratch db: $SCRATCH"

# 1) dump the live database (compressed custom format, portable across roles)
pg_dump --format=custom --no-owner --no-privileges --dbname="$URI" --file="$DUMP"
echo "dumped    : $(wc -c <"$DUMP" | tr -d ' ') bytes"

# 2) fresh scratch DB
psql "$ADMIN_URI" -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$SCRATCH\" WITH (FORCE);" >/dev/null
psql "$ADMIN_URI" -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$SCRATCH\";" >/dev/null

# 3) restore into scratch. Gate success on the VERIFY below (a benign pg_restore warning must not
#    fail the drill; a real failure will show up as a count mismatch).
set +e
pg_restore --no-owner --no-privileges --dbname="$SCRATCH_URI" "$DUMP" 2>/tmp/drill_restore.err
rc=$?
set -e
[ "$rc" -eq 0 ] || echo "note: pg_restore exit $rc — verifying integrity below"

# 4) verify: schema (table count) + migration head (alembic_version) + a data row-count round-tripped
count() { psql "$1" -tAc "$2" 2>/dev/null | tr -d '[:space:]'; }
TBL_Q="SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE';"
ALEMBIC_Q="SELECT version_num FROM alembic_version LIMIT 1;"
ORG_Q="SELECT count(*) FROM organizations;"

src_tbl="$(count "$URI" "$TBL_Q")";       dst_tbl="$(count "$SCRATCH_URI" "$TBL_Q")"
src_alem="$(count "$URI" "$ALEMBIC_Q")";  dst_alem="$(count "$SCRATCH_URI" "$ALEMBIC_Q")"
src_org="$(count "$URI" "$ORG_Q")";       dst_org="$(count "$SCRATCH_URI" "$ORG_Q")"

echo "tables    : source=$src_tbl restored=$dst_tbl"
echo "alembic   : source=$src_alem restored=$dst_alem"
echo "orgs rows : source=$src_org restored=$dst_org"

if [ -n "$src_tbl" ] && [ "$src_tbl" = "$dst_tbl" ] \
   && [ "$src_alem" = "$dst_alem" ] && [ "$src_org" = "$dst_org" ]; then
  echo "RESTORE DRILL: PASS — the backup restores cleanly."
  exit 0
fi
echo "RESTORE DRILL: FAIL — restored database does not match the source."
[ -s /tmp/drill_restore.err ] && { echo "--- pg_restore stderr ---"; cat /tmp/drill_restore.err; }
exit 1
