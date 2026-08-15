#!/usr/bin/env bash
# Production deploy (PILOT-1A). Runs ON the pilot host, not in CI.
#
# Handles BOTH paths with the same sequence:
#
#   first install — empty VPS, no volumes, no database, no roles
#   repeat deploy — existing stack, new code, possibly new migrations
#
# The first draft of this script only worked for the second. It ran migrations with
# `compose run --no-deps` and verified the runtime role with `compose exec postgres`, both of which
# assume Postgres is *already running* — true on an upgrade, false on the very first deploy, which
# is the one deploy nobody gets to practise. Data services are now started and waited for before
# anything needs them, which is correct for both paths and costs an already-running stack nothing.
#
# Order, and why:
#   1. secrets      — a container without them refuses to boot, so failing here is cheapest
#   2. image        — one immutable artifact for api/worker/scheduler
#   3. data         — Postgres + Redis up and HEALTHY before anything connects
#   4. role         — app_rw exists and is NOBYPASSRLS (verified, not assumed)
#   5. migrate      — as the OWNER role, before new code serves traffic
#   6. app          — api/worker/scheduler, then Caddy
#   7. verify       — readiness, not "docker says running"
#
# Migrating after the swap would let new code meet an old schema. Migrating as app_rw would fail,
# because app_rw deliberately has no DDL rights — and the fix must never be to grant them.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ENVIRONMENT="${1:-prod}"
COMPOSE="infra/docker/docker-compose.prod.yml"
dc() { docker compose -f "$COMPOSE" "$@"; }

echo "==> [1/7] decrypting secrets ($ENVIRONMENT)"
eval "$(scripts/decrypt-secrets.sh "$ENVIRONMENT")"
[ -f "${GROWTH_OPERATOR_SECRETS_FILE:?}" ] || { echo "FATAL: no decrypted secrets file" >&2; exit 1; }

echo "==> [2/7] building the application image"
dc build api

echo "==> [3/7] starting data services"
# Explicitly, and waited for. On a first install this creates the volumes and runs the initdb role
# bootstrap; on a repeat deploy it is a no-op that returns immediately.
#
# MinIO is here rather than treated as optional: catalog images have no durable fallback outside
# dev, so an application that starts before storage is ready starts broken.
dc up -d postgres redis minio
for svc in postgres redis minio; do
  for i in $(seq 1 60); do
    if [ "$(dc ps --format '{{.Health}}' "$svc" 2>/dev/null | head -1)" = "healthy" ]; then break; fi
    [ "$i" = "60" ] && { echo "FATAL: $svc did not become healthy" >&2; dc logs --tail 40 "$svc" >&2; exit 1; }
    sleep 2
  done
  echo "$svc healthy"
done

echo "==> [4/7] verifying the runtime role"
# The role is created by an initdb script, which runs only on a FRESH volume. On an existing volume
# that step is silently skipped, so verify rather than assume: a missing app_rw means every runtime
# connection fails, and an app_rw WITH BypassRLS means tenant isolation is not being enforced at
# all — the second is far worse and completely invisible.
role="$(dc exec -T postgres psql -U "${POSTGRES_USER:?}" -d "${POSTGRES_DB:-growth_operator}" \
        -tAc "SELECT rolname || ':' || rolbypassrls FROM pg_roles WHERE rolname = 'app_rw'" | tr -d '[:space:]')"
case "$role" in
  app_rw:f) echo "app_rw present, NOBYPASSRLS confirmed" ;;
  app_rw:t) echo "FATAL: app_rw has BYPASSRLS — row-level security would not be enforced" >&2; exit 1 ;;
  *)        echo "FATAL: app_rw is missing. On an existing volume the initdb bootstrap does not" >&2
            echo "       re-run; create it with infra/db/roles-prod.sh (APP_RW_PASSWORD set)." >&2
            exit 1 ;;
esac

echo "==> [5/7] migrating (owner role)"
dc run --rm --no-deps \
  -e GROWTH_OPERATOR_DATABASE_URL="${GROWTH_OPERATOR_DATABASE_MIGRATOR_URL:?}" \
  api alembic upgrade head

echo "==> [6/7] starting application services"
dc up -d --remove-orphans

echo "==> [7/7] waiting for readiness"
for i in $(seq 1 30); do
  if dc exec -T api curl -fsS http://127.0.0.1:8000/readyz >/dev/null 2>&1; then
    echo "ready after ${i} attempt(s)"
    exec scripts/pilot-health-check.sh
  fi
  sleep 5
done

echo "::error::did not become ready — nothing is rolled back automatically; inspect before retrying" >&2
dc logs --tail 50 api >&2
exit 1
