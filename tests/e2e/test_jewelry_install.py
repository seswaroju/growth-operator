"""Reference jewelry install (MVP-041) — the permanent CI check.

Installs the jewelry pack for a fresh org from `verticals/jewelry/install.yaml` and asserts the
`expected_result` block field-by-field (status, paused instances, catalog schema version,
candidate prompt layers, bindings, deferred steps) within the 60s budget. Skips when the DB is
unreachable.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
import yaml

from core.common import db as dbmod
from core.common.config import get_settings
from core.packs.installer import install

JEWELRY = Path(__file__).resolve().parents[2] / "verticals" / "jewelry"
INSTALL_SPEC = yaml.safe_load((JEWELRY / "install.yaml").read_text())


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.pack_installations')"))
    finally:
        await conn.close()


@pytest.fixture()
async def org() -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres/packs not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    o = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'JW')", o)
    finally:
        await conn.close()
    yield o
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", o)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        # deleting the org cascades its instances + installations
        await conn.execute("DELETE FROM organizations WHERE id=$1", o)
        pid = await conn.fetchval("SELECT id FROM packs WHERE slug='jewelry'")
        if pid and not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pack_installations WHERE pack_id=$1)", pid
        ):
            for t in ("prompt_layers", "agent_bindings", "catalog_schemas"):
                await conn.execute(f"DELETE FROM {t} WHERE pack_id=$1", pid)
            await conn.execute("DELETE FROM packs WHERE id=$1", pid)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_reference_jewelry_install_matches_expected_result(org: uuid.UUID) -> None:
    expected = INSTALL_SPEC["expected_result"]
    started = time.monotonic()
    result = await install(org, JEWELRY, INSTALL_SPEC.get("config", {}))
    elapsed = time.monotonic() - started

    assert result.status == expected["status"]
    assert list(result.deferred_steps) == expected["deferred_steps"]
    assert elapsed < 60  # install completes well within the CI budget

    conn = await asyncpg.connect(_dsn())
    try:
        pid = await conn.fetchval("SELECT id FROM packs WHERE slug='jewelry'")
        instances = await conn.fetch(
            "SELECT status FROM agent_instances WHERE org_id=$1", org
        )
        assert len(instances) == expected["instances_paused"]
        assert all(r["status"] == "paused" for r in instances)

        assert await conn.fetchval(
            "SELECT version FROM catalog_schemas WHERE pack_id=$1", pid
        ) == expected["catalog_schema_version"]

        layers = await conn.fetch(
            "SELECT status FROM prompt_layers WHERE pack_id=$1", pid
        )
        assert len(layers) == expected["prompt_layers_candidate"]
        assert all(r["status"] == "candidate" for r in layers)

        assert await conn.fetchval(
            "SELECT count(*) FROM agent_bindings WHERE pack_id=$1", pid
        ) == expected["bindings"]

        assert await conn.fetchval(
            "SELECT status FROM pack_installations WHERE id=$1", result.installation_id
        ) == "active"
    finally:
        await conn.close()
