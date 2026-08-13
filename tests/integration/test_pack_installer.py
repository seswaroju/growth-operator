"""Transactional pack installer (MVP-040) against real Postgres under app_rw.

Installs a (uniquely-slugged copy of the) jewelry pack for a fresh org and checks: paused
instances + candidate prompt layers + registered catalog schema + bindings, digest
idempotency (reinstall = no-op), rollback (failure injected at each of the six steps leaves
zero partial artifact rows and marks the install failed at that step), and uninstall (instances
re-paused, catalog schema retained, L3 data untouched). Skips when the DB is unreachable.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.packs import installer
from core.packs.installer import InstallError, install, list_packs, uninstall

VERTICALS = Path(__file__).resolve().parents[2] / "verticals"

STEP_FN = {
    "catalog_schema": "_register_catalog_schema",
    "pack_migrations": "_apply_pack_migrations",
    "prompt_layers": "_seed_prompt_layers",
    "policies": "_seed_policies",
    "workflows": "_seed_workflows",
    "bindings_instances": "_create_bindings_and_instances",
}


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
async def scene(tmp_path: Path) -> AsyncIterator[dict]:
    if not await _db_ready():
        pytest.skip("Postgres/packs (008) not ready")
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
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'P')", org)
    finally:
        await conn.close()
    yield {"org": org, "slug": slug, "pack_dir": pack_dir}

    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)  # cascades L3 + instances
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


async def _counts(org: uuid.UUID, slug: str) -> dict[str, int]:
    conn = await asyncpg.connect(_dsn())
    try:
        pid = await conn.fetchval("SELECT id FROM packs WHERE slug=$1", slug)
        return {
            "instances": await conn.fetchval(
                "SELECT count(*) FROM agent_instances WHERE org_id=$1", org
            ),
            "layers": await conn.fetchval(
                "SELECT count(*) FROM prompt_layers WHERE pack_id=$1", pid
            ) if pid else 0,
            "schemas": await conn.fetchval(
                "SELECT count(*) FROM catalog_schemas WHERE pack_id=$1", pid
            ) if pid else 0,
            "bindings": await conn.fetchval(
                "SELECT count(*) FROM agent_bindings WHERE pack_id=$1", pid
            ) if pid else 0,
        }
    finally:
        await conn.close()


async def test_install_seeds_paused_instances_and_candidate_layers(scene: dict) -> None:
    org, slug, pack_dir = scene["org"], scene["slug"], scene["pack_dir"]
    result = await install(org, pack_dir)
    assert result.status == "active" and result.idempotent is False
    assert result.deferred_steps == ()  # policies (MVP-044) + workflows (MVP-072) both seeded

    c = await _counts(org, slug)
    assert c["instances"] == 4 and c["bindings"] == 4  # support archetype not seeded → skipped
    assert c["schemas"] == 1 and c["layers"] == 10
    conn = await asyncpg.connect(_dsn())
    try:  # pack tier rules seeded into approval_policies (all archetypes' tier_defaults)
        # 8 base rules + the LP-2d campaigner `landing_publish` rule = 9
        assert await conn.fetchval(
            "SELECT count(*) FROM approval_policies WHERE scope='pack' "
            "AND pack_id=(SELECT id FROM packs WHERE slug=$1)", slug) == 9
    finally:
        await conn.close()

    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval(
            "SELECT bool_and(status='paused') FROM agent_instances WHERE org_id=$1", org
        ) is True
        assert await conn.fetchval(
            "SELECT bool_and(status='candidate') FROM prompt_layers "
            "WHERE pack_id=(SELECT id FROM packs WHERE slug=$1)", slug
        ) is True
        assert await conn.fetchval(
            "SELECT count(*) FROM audit_log WHERE org_id=$1 AND action='pack.installed'", org
        ) == 1
        assert await conn.fetchval(
            "SELECT status FROM pack_installations WHERE id=$1", result.installation_id
        ) == "active"
    finally:
        await conn.close()


async def test_reinstall_same_digest_is_noop(scene: dict) -> None:
    org, slug, pack_dir = scene["org"], scene["slug"], scene["pack_dir"]
    first = await install(org, pack_dir)
    second = await install(org, pack_dir)
    assert second.idempotent is True and second.installation_id == first.installation_id
    assert (await _counts(org, slug))["instances"] == 4  # no duplicates
    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval(
            "SELECT count(*) FROM pack_installations WHERE org_id=$1", org
        ) == 1
    finally:
        await conn.close()


@pytest.mark.parametrize("step", list(STEP_FN))
async def test_failure_at_each_step_rolls_back_fully(
    scene: dict, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    org, slug, pack_dir = scene["org"], scene["slug"], scene["pack_dir"]

    async def _boom(session: object, ctx: object) -> None:
        raise RuntimeError(f"injected failure at {step}")

    monkeypatch.setattr(installer, STEP_FN[step], _boom)
    with pytest.raises(InstallError) as ei:
        await install(org, pack_dir)
    assert ei.value.step == step

    c = await _counts(org, slug)
    assert c == {"instances": 0, "layers": 0, "schemas": 0, "bindings": 0}  # zero partial state
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT status, config FROM pack_installations WHERE org_id=$1", org
        )
        assert row["status"] == "failed"
        import json
        assert json.loads(row["config"])["_error_step"] == step
    finally:
        await conn.close()


async def test_uninstall_pauses_instances_retains_schema_and_l3(scene: dict) -> None:
    org, slug, pack_dir = scene["org"], scene["slug"], scene["pack_dir"]
    result = await install(org, pack_dir)

    conn = await asyncpg.connect(_dsn())
    try:
        # An L3 row (a contact) and an activated instance, to prove uninstall's effects.
        await conn.execute(
            "INSERT INTO contacts (org_id, phone) VALUES ($1,'+15550000000')", org
        )
        await conn.execute("UPDATE agent_instances SET status='active' WHERE org_id=$1", org)
    finally:
        await conn.close()

    await uninstall(org, result.installation_id)

    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval(
            "SELECT bool_and(status='paused') FROM agent_instances WHERE org_id=$1", org
        ) is True  # re-paused
        assert await conn.fetchval(
            "SELECT status FROM pack_installations WHERE id=$1", result.installation_id
        ) == "uninstalled"
        assert await conn.fetchval(
            "SELECT count(*) FROM catalog_schemas "
            "WHERE pack_id=(SELECT id FROM packs WHERE slug=$1)", slug
        ) == 1  # schema retained
        assert await conn.fetchval(
            "SELECT count(*) FROM contacts WHERE org_id=$1", org
        ) == 1  # L3 untouched
    finally:
        await conn.close()


async def test_list_packs_returns_published(scene: dict) -> None:
    org, pack_dir = scene["org"], scene["pack_dir"]
    await install(org, pack_dir)
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        packs = await list_packs(s)
    assert any(p["slug"] == scene["slug"] and p["status"] == "published" for p in packs)
