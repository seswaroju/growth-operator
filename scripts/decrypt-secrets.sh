#!/usr/bin/env bash
# Decrypt secrets/<env>.enc.yaml to a runtime plaintext file and print an export line for
# GROWTH_OPERATOR_SECRETS_FILE (MVP-008). Fails loudly on a missing tool / key / file so a
# container never boots with insecure defaults.
#
# Usage:  eval "$(scripts/decrypt-secrets.sh dev)"
set -euo pipefail

ENV="${1:-}"
if [[ -z "$ENV" ]]; then
  echo "usage: decrypt-secrets.sh <dev|staging|prod>" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENC="$ROOT/secrets/${ENV}.enc.yaml"
OUT="${GROWTH_OPERATOR_SECRETS_FILE:-/run/secrets/growth-operator.${ENV}.yaml}"

command -v sops >/dev/null 2>&1 || { echo "FATAL: sops not installed (brew install sops age)" >&2; exit 1; }
[[ -f "$ENC" ]] || { echo "FATAL: encrypted secrets file not found: $ENC" >&2; exit 1; }

mkdir -p "$(dirname "$OUT")"
if ! sops --decrypt "$ENC" > "$OUT" 2>/tmp/sops.err; then
  echo "FATAL: SOPS decryption failed (age key missing or wrong?):" >&2
  cat /tmp/sops.err >&2
  rm -f "$OUT"
  exit 1
fi
chmod 600 "$OUT"
echo "export GROWTH_OPERATOR_SECRETS_FILE=$OUT"
