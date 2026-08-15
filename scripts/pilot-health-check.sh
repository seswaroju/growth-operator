#!/usr/bin/env bash
# One command that answers "is the pilot actually up?" (PILOT-1A).
#
# Written for the founder, not for a dashboard: every line is a question worth asking at 11pm when
# a merchant says nothing is working. It reads only existing endpoints and container state — there
# is no monitoring product here, and there should not be one for a single-tenant pilot.
#
#   scripts/pilot-health-check.sh                      # against api.vaylorn.com
#   API=https://api.example.com scripts/pilot-health-check.sh
set -uo pipefail

API="${API:-https://api.vaylorn.com}"
COMPOSE="${COMPOSE:-infra/docker/docker-compose.prod.yml}"
fail=0

ok()   { printf "  \033[32mOK\033[0m    %s\n" "$1"; }
bad()  { printf "  \033[31mFAIL\033[0m  %s\n" "$1"; fail=1; }
note() { printf "  ----  %s\n" "$1"; }

echo "Vaylorn pilot health — $API"

# --- API liveness + readiness -------------------------------------------------------------
if curl -fsS --max-time 10 "$API/healthz" >/dev/null 2>&1; then ok "API process is up"
else bad "API is not answering /healthz"; fi

ready="$(curl -fsS --max-time 15 "$API/readyz" 2>/dev/null || echo '')"
if [ -n "$ready" ]; then
  ok "API is ready"
  # /readyz already reports Postgres, Redis and whether the schema is at head — three of the
  # founder's questions answered by one endpoint that existed before this ticket.
  echo "$ready" | tr ',' '\n' | sed 's/[{}"]//g' | sed 's/^/        /'
else
  bad "API is not ready — Postgres, Redis or the schema is behind (see /readyz)"
fi

# --- TLS ----------------------------------------------------------------------------------
host="${API#https://}"; host="${host%%/*}"
if expiry=$(echo | openssl s_client -servername "$host" -connect "$host:443" 2>/dev/null \
            | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2); then
  [ -n "$expiry" ] && ok "TLS certificate valid until $expiry" || bad "no TLS certificate"
else
  bad "TLS handshake failed for $host"
fi

# --- long-running processes ---------------------------------------------------------------
if command -v docker >/dev/null 2>&1 && [ -f "$COMPOSE" ]; then
  for svc in api worker scheduler postgres redis minio caddy; do
    state="$(docker compose -f "$COMPOSE" ps --format '{{.State}}' "$svc" 2>/dev/null | head -1)"
    case "$state" in
      running) ok "$svc is running" ;;
      "")      bad "$svc is not present" ;;
      *)       bad "$svc is $state" ;;
    esac
  done
  # A worker that is "running" but not consuming is the failure that looks fine. Recent log lines
  # are the cheapest evidence that it is actually doing something.
  note "recent worker activity:"
  docker compose -f "$COMPOSE" logs --tail 3 --no-log-prefix worker 2>/dev/null | sed 's/^/        /'
else
  note "docker/compose not available here — skipping container checks"
fi

# --- external boundaries ------------------------------------------------------------------
# Deliberately reported from logs rather than probed: probing Meta or an LLM vendor to check
# health would itself be an external call, and this script must be safe to run at any time.
if command -v docker >/dev/null 2>&1 && [ -f "$COMPOSE" ]; then
  hooks=$(docker compose -f "$COMPOSE" logs --since 24h api 2>/dev/null | grep -c "webhooks/whatsapp" || true)
  note "Meta webhook hits (24h): ${hooks:-0}"
  llm=$(docker compose -f "$COMPOSE" logs --since 24h worker 2>/dev/null | grep -ci "provider_unavailable\|provider call failed" || true)
  note "LLM provider failures (24h): ${llm:-0}"
  sends=$(docker compose -f "$COMPOSE" logs --since 24h worker 2>/dev/null | grep -ci "recovery.*fail\|msg.failed" || true)
  note "recovery/send failures (24h): ${sends:-0}"
fi

echo
[ "$fail" -eq 0 ] && echo "pilot looks healthy" || echo "PROBLEMS FOUND — see FAIL lines above"
exit "$fail"
