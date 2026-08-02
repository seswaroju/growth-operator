"""Catalog index generation (MVP-042).

Pack attribute indexes are **generated** from the schema's `x-index` annotations, never
hand-written: at registration the installer stores the CREATE INDEX statements in
`catalog_schemas.generated_ddl`, and a scheduler job applies them CONCURRENTLY. Each is a
**partial expression index** on the jsonb `attributes` bag, scoped to the pack and to rows that
carry the field; numeric attributes (`x-index-type: numeric`) get a typed cast, and array
attributes get a GIN containment index.

CONCURRENTLY can't run inside a transaction, so the apply job uses an autocommit privileged
connection (the migrator role has DDL rights; app_rw does not) with a 3s `lock_timeout` — a
contended index is simply left for the next (off-peak) run, since each statement is
`IF NOT EXISTS`.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger("core.packs.indexes")

DEFAULT_LOCK_TIMEOUT_MS = 3000


def _index_name(pack_slug: str, field: str) -> str:
    return f"idx_cat_{pack_slug}_{field}"


def generate_index_ddl(pack_slug: str, pack_id: UUID, json_schema: dict[str, Any]) -> list[str]:
    """CREATE INDEX statements for every `x-index` attribute (deterministic, sorted by field)."""
    ddl: list[str] = []
    properties = json_schema.get("properties", {})
    for field in sorted(properties):
        spec = properties[field]
        if not spec.get("x-index"):
            continue
        name = _index_name(pack_slug, field)
        head = f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON catalog_items"
        scope = f"WHERE pack_id = '{pack_id}' AND attributes ? '{field}'"
        if spec.get("type") == "array":
            ddl.append(f"{head} USING gin ((attributes->'{field}')) WHERE pack_id = '{pack_id}'")
        elif spec.get("x-index-type") == "numeric":
            ddl.append(f"{head} (((attributes->>'{field}')::numeric)) {scope}")
        else:
            ddl.append(f"{head} ((attributes->>'{field}')) {scope}")
    return ddl


async def apply_generated_indexes(
    *, lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS
) -> tuple[int, int]:
    """Apply every schema's generated_ddl CONCURRENTLY. Returns (applied, deferred); a statement
    that hits the lock_timeout is deferred (retried on the next run — each is IF NOT EXISTS)."""
    import asyncpg

    from core.common.config import get_settings

    dsn = get_settings().database_migrator_url.replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    applied = deferred = 0
    try:
        rows = await conn.fetch("SELECT generated_ddl FROM catalog_schemas")
        statements = [stmt for row in rows for stmt in (row["generated_ddl"] or [])]
        await conn.execute(f"SET lock_timeout = '{lock_timeout_ms}ms'")
        for stmt in statements:
            try:
                await conn.execute(stmt)  # autocommit → CONCURRENTLY is allowed
                applied += 1
            except Exception as exc:  # noqa: BLE001 - lock contention etc. → defer to next run
                logger.warning("index apply deferred (%s): %s", stmt.split()[6], exc)
                deferred += 1
    finally:
        await conn.close()
    return applied, deferred
