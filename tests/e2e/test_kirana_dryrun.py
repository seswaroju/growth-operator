"""Kirana dry-run CI gate (MVP-043) — the second pack installs with zero core changes.

Runs the installer's dry-run (full pipeline inside a rolled-back transaction) for kirana and
asserts the `expected_plan` from `verticals/kirana/install.yaml`, then verifies **nothing was
persisted** (no kirana pack row, no instances). A jewelry-specific hardcode in core would make
the dry-run fail here. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
import yaml

from core.common import db as dbmod
from core.common.config import get_settings
from core.packs.installer import dry_run

KIRANA = Path(__file__).resolve().parents[2] / "verticals" / "kirana"
SPEC = yaml.safe_load((KIRANA / "install.yaml").read_text())


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
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'KR')", o)
    finally:
        await conn.close()
    yield o
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM organizations WHERE id=$1", o)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_kirana_dry_run_matches_plan_and_writes_nothing(org: uuid.UUID) -> None:
    expected = SPEC["expected_plan"]
    plan = await dry_run(org, KIRANA)

    assert plan.pack == expected["pack"]
    assert plan.catalog_schema_version == expected["catalog_schema_version"]
    assert plan.prompt_layers == expected["prompt_layers"]
    assert plan.bindings == expected["bindings"] and plan.instances == expected["instances"]
    assert plan.workflows == expected["workflows"]
    assert plan.integrations == expected["integrations"]
    assert list(plan.deferred_steps) == expected["deferred_steps"]

    # Dry-run persists nothing — the pipeline transaction is always rolled back.
    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval("SELECT count(*) FROM packs WHERE slug='kirana'") == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_instances WHERE org_id=$1", org
        ) == 0
    finally:
        await conn.close()
