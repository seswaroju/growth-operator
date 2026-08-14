#!/usr/bin/env bash
# Build the merchant and operator bundles for production (PILOT-1A).
#
# `VITE_API_BASE` is baked in at BUILD time, not read at runtime — Vite performs a textual
# substitution, so the value must be correct here or the deployed app quietly calls localhost from
# a customer's browser and every request fails CORS with no useful error. The build therefore
# refuses to finish if a localhost base survives into the output.
#
#   scripts/build-frontend.sh                       # https://api.vaylorn.com
#   VITE_API_BASE=https://api.example.com scripts/build-frontend.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_BASE="${VITE_API_BASE:-https://api.vaylorn.com}"

case "$API_BASE" in
  https://*) ;;
  *) echo "FATAL: VITE_API_BASE must be https (got: $API_BASE)" >&2; exit 1 ;;
esac

for app in web web-ops; do
  echo "==> building $app against $API_BASE"
  ( cd "$ROOT/$app" && npm ci --silent && VITE_API_BASE="$API_BASE" npm run build )

  # Proof, not assumption: grep the emitted bundle. A build that silently kept the development
  # default produces an app that cannot talk to its own API, and the failure only appears in a
  # merchant's browser.
  if grep -rqE "localhost:8000|127\.0\.0\.1:8000" "$ROOT/$app/dist"; then
    echo "FATAL: $app/dist still references a localhost API base — refusing to ship" >&2
    exit 1
  fi
  echo "    ok: no localhost API base in $app/dist"
done

echo "frontend bundles ready (web/dist, web-ops/dist)"
