#!/usr/bin/env bash
# Regenerate the schema documentation from the LIVE migrated database.
#
# The migrations are the source of truth (applied + tested + CI-gated); this emits a canonical
# snapshot of the resulting schema — tables, constraints, indexes AND row-level-security policies —
# so docs/06-database/schema.sql can never silently drift again. Run it after adding a migration.
#
# Usage:
#   scripts/dump_schema.sh              # → stdout (review it)
#   scripts/dump_schema.sh out.sql      # → a file
#   make schema-doc                     # → writes docs/06-database/schema.sql (the vault)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

OUT="${1:-/dev/stdout}"

# pg_dump ships with libpq, which is keg-only on macOS (not on PATH) — find it either way.
PG_DUMP="$(command -v pg_dump || true)"
if [ -z "$PG_DUMP" ] && command -v brew >/dev/null 2>&1; then
  PG_DUMP="$(brew --prefix libpq 2>/dev/null)/bin/pg_dump"
fi
if [ -z "$PG_DUMP" ] || [ ! -x "$PG_DUMP" ]; then
  echo "pg_dump not found — install it with: brew install libpq" >&2
  exit 1
fi

# The owner/migrator DSN (full schema visibility), from Settings — strip the +asyncpg driver.
DSN="$(uv run python -c 'from core.common.config import get_settings; print(get_settings().database_migrator_url.replace("+asyncpg",""))')"

"$PG_DUMP" --schema-only --no-owner --no-privileges --schema=public "$DSN" > "$OUT"
[ "$OUT" != "/dev/stdout" ] && echo "wrote schema → $OUT (review the diff, then save in Obsidian)" >&2
