#!/usr/bin/env bash
# MVP-010 architecture lint guards. Run from anywhere: `uv run bash scripts/lint.sh`.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# core must not import verticals. guards.py enforces this via grep; if import-linter is
# installed, its .importlinter contract is checked too (belt and braces).
if command -v lint-imports >/dev/null 2>&1; then
  lint-imports
fi

python scripts/guards.py
