"""Manifest recompile + proxy freshness/tamper enforcement (MVP-061) against real Postgres.

`recompile_instance` compiles archetype ∩ pack, signs it, and pins it on the instance. The proxy
then denies a **stale** pinned manifest (after a recompile) until re-pinned, and a **tampered**
manifest fails the signature check — three such violations abort the run. Skips when DB unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.mediation import manifest as m
from core.mediation import proxy
from core.mediation.proxy import RunAborted, RunContext
from core.tenancy.middleware import org_scoped_session

TOOL = "x.action"


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.agent_instances')"))
    finally:
        await conn.close()


class FakeRedis:
    def __init__(self) -> None:
        self.streams: list[Any] = []
        self.counters: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, *a: Any) -> bool:
        return True

    async def get(self, key: str) -> Any:
        return None

    async def xadd(self, stream: str, fields: Any) -> str:
        self.streams.append((stream, fields))
        return "1-1"


class Scene:
    def __init__(self, org: uuid.UUID, instance: uuid.UUID) -> None:
        self.org = org
        self.instance = instance


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/runtime not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'MC')", org)
        pack = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"mc{org.hex[:8]}")
        arch = await conn.fetchval(
            "INSERT INTO agent_archetypes (slug, capability_allowlist) VALUES ($1,$2) RETURNING id",
            f"a_{org.hex[:8]}", [TOOL, "catalog.search"])
        binding = await conn.fetchval(
            "INSERT INTO agent_bindings (pack_id, archetype_id, persona_default, tool_grants, "
            " kpi_defs, tier_defaults) VALUES ($1,$2,'p',$3::jsonb,'{}'::jsonb,'[]'::jsonb) "
            "RETURNING id", pack, arch,
            json.dumps([{"name": TOOL}, {"name": "catalog.search"}]))
        instance = await conn.fetchval(
            "INSERT INTO agent_instances (org_id, binding_id, persona_name, status, "
            " permission_manifest, budget_caps) "
            "VALUES ($1,$2,'p','active','{}'::jsonb,'{}'::jsonb) RETURNING id", org, binding)
    finally:
        await conn.close()
    yield Scene(org, instance)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM agent_instances WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_bindings WHERE pack_id=$1", pack)
        await conn.execute("DELETE FROM agent_archetypes WHERE id=$1", arch)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _recompile(scene: Scene) -> dict:
    async with org_scoped_session(scene.org) as s:
        man = await m.recompile_instance(s, scene.org, scene.instance)
        await s.commit()
    return man


def _ctx(scene: Scene, man: dict) -> RunContext:
    return RunContext(org_id=scene.org, run_id=uuid.uuid4(), instance_id=scene.instance,
                      manifest=man, manifest_hash=m.manifest_hash(man))


async def test_recompile_pins_a_signed_intersection(scene: Scene) -> None:
    man = await _recompile(scene)
    assert m.verify(man) is True
    assert {t["name"] for t in man["tools"]} == {TOOL, "catalog.search"}  # archetype ∩ pack
    conn = await asyncpg.connect(_dsn())
    try:
        stored = json.loads(await conn.fetchval(
            "SELECT permission_manifest FROM agent_instances WHERE id=$1", scene.instance))
    finally:
        await conn.close()
    assert stored["hash"] == man["hash"]  # pinned on the instance


async def test_stale_manifest_denied_until_recompile(scene: Scene) -> None:
    man1 = await _recompile(scene)
    async with org_scoped_session(scene.org) as s:  # fresh pin → not a manifest denial
        fresh = await proxy.call(_ctx(scene, man1), TOOL, {}, session=s, redis=FakeRedis())
        await s.commit()
    assert fresh.error is None or fresh.error.code != "permission_denied_manifest"

    man2 = await _recompile(scene)  # recompile bumps compiled_at → new hash; man1 is now stale
    assert man2["hash"] != man1["hash"]
    async with org_scoped_session(scene.org) as s:
        stale = await proxy.call(_ctx(scene, man1), TOOL, {}, session=s, redis=FakeRedis())
        await s.commit()
    assert stale.error is not None and stale.error.code == "permission_denied_manifest"

    async with org_scoped_session(scene.org) as s:  # re-pinned to the current manifest → allowed
        ok = await proxy.call(_ctx(scene, man2), TOOL, {}, session=s, redis=FakeRedis())
        await s.commit()
    assert ok.error is None or ok.error.code != "permission_denied_manifest"


async def test_tampered_manifest_denied_and_aborts_after_three(scene: Scene) -> None:
    man = await _recompile(scene)
    tampered = dict(man)
    tampered["tools"] = [{"name": TOOL, "requires_tier_eval": True, "injected": "evil"}]  # bit flip
    ctx = _ctx(scene, tampered)  # manifest_hash recomputed over the tampered body → sig invalid
    redis = FakeRedis()
    async with org_scoped_session(scene.org) as s:
        first = await proxy.call(ctx, TOOL, {}, session=s, redis=redis)
        await proxy.call(ctx, TOOL, {}, session=s, redis=redis)
        with pytest.raises(RunAborted):  # 3rd manifest violation aborts the run
            await proxy.call(ctx, TOOL, {}, session=s, redis=redis)
        await s.commit()
    assert first.error is not None and first.error.code == "permission_denied_manifest"
