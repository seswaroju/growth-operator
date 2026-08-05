"""Catalog index generation + apply (MVP-042).

Snapshots the DDL generated from the jewelry schema's x-index annotations (verbatim), then
installs a pack and verifies the installer stored generated_ddl and that the apply job creates
the partial expression indexes CONCURRENTLY (idempotently). DB parts skip when unreachable.
"""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.packs import indexes
from core.packs.indexes import apply_generated_indexes, generate_index_ddl
from core.packs.installer import install

VERTICALS = Path(__file__).resolve().parents[2] / "verticals"
_FIXED = uuid.UUID("00000000-0000-0000-0000-000000000001")


def test_jewelry_ddl_snapshot() -> None:
    schema = json.loads((VERTICALS / "jewelry" / "catalog" / "schema.json").read_text())
    ddl = generate_index_ddl("jewelry", _FIXED, schema)
    assert ddl == [
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cat_jewelry_category ON catalog_items "
        f"((attributes->>'category')) WHERE pack_id = '{_FIXED}' AND attributes ? 'category'",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cat_jewelry_gender ON catalog_items "
        f"((attributes->>'gender')) WHERE pack_id = '{_FIXED}' AND attributes ? 'gender'",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cat_jewelry_gross_weight_g ON catalog_items "
        f"(((attributes->>'gross_weight_g')::numeric)) WHERE pack_id = '{_FIXED}' "
        "AND attributes ? 'gross_weight_g'",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cat_jewelry_metal ON catalog_items "
        f"((attributes->>'metal')) WHERE pack_id = '{_FIXED}' AND attributes ? 'metal'",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cat_jewelry_occasion ON catalog_items "
        f"USING gin ((attributes->'occasion')) WHERE pack_id = '{_FIXED}'",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cat_jewelry_purity ON catalog_items "
        f"((attributes->>'purity')) WHERE pack_id = '{_FIXED}' AND attributes ? 'purity'",
    ]


def test_only_x_index_fields_generate() -> None:
    schema = {"properties": {
        "a": {"x-index": True}, "b": {"type": "string"}, "c": {"x-index": True, "type": "array"},
    }}
    names = [d.split()[6] for d in generate_index_ddl("p", _FIXED, schema)]
    assert names == ["idx_cat_p_a", "idx_cat_p_c"]  # 'b' has no x-index


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(
            await conn.fetchval(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name='catalog_schemas' AND column_name='generated_ddl'"
            )
        )
    finally:
        await conn.close()


@pytest.fixture()
async def installed(tmp_path: Path) -> AsyncIterator[dict]:
    if not await _db_ready():
        pytest.skip("Postgres/generated_ddl (1b9dc38df16c) not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    slug = f"jw{org.hex[:8]}"
    pack_dir = tmp_path / "pack"
    shutil.copytree(VERTICALS / "jewelry", pack_dir)
    pk = pack_dir / "pack.yaml"
    pk.write_text(pk.read_text().replace("pack: jewelry", f"pack: {slug}", 1))
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'IX')", org)
    finally:
        await conn.close()
    await install(org, pack_dir)
    yield {"org": org, "slug": slug}
    conn = await asyncpg.connect(_dsn())
    try:
        idx = await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE indexname LIKE $1", f"idx_cat_{slug}_%"
        )
        for r in idx:
            await conn.execute(f'DROP INDEX IF EXISTS {r["indexname"]}')
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        pid = await conn.fetchval("SELECT id FROM packs WHERE slug=$1", slug)
        if pid:
            for t in ("prompt_layers", "approval_policies", "agent_bindings", "catalog_schemas"):
                await conn.execute(f"DELETE FROM {t} WHERE pack_id=$1", pid)
            await conn.execute("DELETE FROM packs WHERE id=$1", pid)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_installer_stores_generated_ddl(installed: dict) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        ddl = await conn.fetchval(
            "SELECT generated_ddl FROM catalog_schemas WHERE pack_id="
            "(SELECT id FROM packs WHERE slug=$1)",
            installed["slug"],
        )
    finally:
        await conn.close()
    assert len(ddl) == 6 and all(installed["slug"] in d for d in ddl)


async def test_apply_creates_indexes_idempotently(installed: dict) -> None:
    slug = installed["slug"]
    applied, deferred = await apply_generated_indexes()
    assert applied >= 6 and deferred == 0

    conn = await asyncpg.connect(_dsn())
    try:
        names = {
            r["indexname"]
            for r in await conn.fetch(
                "SELECT indexname FROM pg_indexes WHERE indexname LIKE $1", f"idx_cat_{slug}_%"
            )
        }
    finally:
        await conn.close()
    assert f"idx_cat_{slug}_purity" in names and f"idx_cat_{slug}_gross_weight_g" in names

    # Re-apply is a no-op (IF NOT EXISTS) and must not error.
    again, _ = await apply_generated_indexes()
    assert again >= 6


def test_lock_timeout_is_configurable() -> None:
    assert indexes.DEFAULT_LOCK_TIMEOUT_MS == 3000
