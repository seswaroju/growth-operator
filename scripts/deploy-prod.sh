#!/usr/bin/env bash
# Production deploy (PILOT-1A). Runs ON the pilot host, not in CI.
#
# Order matters and is the whole point of this script existing:
#   1. decrypt secrets   — a container that boots without them refuses to start (by design)
#   2. build the image   — one immutable artifact for api/worker/scheduler
#   3. migrate           — as the OWNER role, before any new code serves traffic
#   4. swap containers   — runtime processes connect as app_rw (NOBYPASSRLS)
#   5. verify            — readiness, not just "docker says running"
#
# Migrating after the swap would let new code meet an old schema; migrating as app_rw would fail,
# because app_rw deliberately has no DDL rights. Both mistakes are easy and neither is loud.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ENVIRONMENT="${1:-prod}"
COMPOSE="infra/docker/docker-compose.prod.yml"

echo "==> [1/5] decrypting secrets ($ENVIRONMENT)"
eval "$(scripts/decrypt-secrets.sh "$ENVIRONMENT")"
[ -f "${GROWTH_OPERATOR_SECRETS_FILE:?}" ] || { echo "FATAL: no decrypted secrets file" >&2; exit 1; }

echo "==> [2/5] building the application image"
docker compose -f "$COMPOSE" build api

echo "==> [3/5] migrating (owner role)"
# Explicitly the migrator URL. app_rw has no DDL rights, so this cannot silently run as the
# runtime role — and the runtime role must never acquire them to make a deploy convenient.
docker compose -f "$COMPOSE" run --rm --no-deps \
  -e GROWTH_OPERATOR_DATABASE_URL="${GROWTH_OPERATOR_DATABASE_MIGRATOR_URL:?}" \
  api alembic upgrade head

# The runtime role is created by an initdb script, which runs only on a FRESH volume. On an
# existing volume that step is silently skipped, so verify rather than assume: an app_rw that does
# not exist means every runtime connection fails, and an app_rw with BYPASSRLS means tenant
# isolation is not being enforced at all — the second is far worse and completely invisible.
echo "==> [3.5/5] verifying the runtime role"
docker compose -f "$COMPOSE" exec -T postgres psql -U "${POSTGRES_USER:?}" \
  -d "${POSTGRES_DB:-growth_operator}" -tAc \
  "SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname = 'app_rw'" | grep -q '^app_rw|f$' || {
    echo "FATAL: app_rw is missing or has BYPASSRLS — refusing to serve traffic" >&2
    echo "       create it with infra/db/roles-prod.sh (APP_RW_PASSWORD must be set)" >&2
    exit 1
  }
echo "app_rw present, NOBYPASSRLS confirmed"

echo "==> [4/5] starting services"
docker compose -f "$COMPOSE" up -d --remove-orphans

echo "==> [5/5] waiting for readiness"
for i in $(seq 1 30); do
  if docker compose -f "$COMPOSE" exec -T api curl -fsS http://127.0.0.1:8000/readyz >/dev/null 2>&1; then
    echo "ready after ${i} attempt(s)"
    exec scripts/pilot-health-check.sh
  fi
  sleep 5
done

echo "::error::did not become ready — rolling nothing back automatically; inspect before retrying" >&2
docker compose -f "$COMPOSE" logs --tail 50 api >&2
exit 1
