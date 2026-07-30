"""Prompt registry against real Postgres under app_rw (MVP-058).

Covers the pin compatibility check (incompatible pin refused), the one-active-binding-per-
(instance, task) invariant, and layer content immutability. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.prompts import registry
from core.prompts.registry import IncompatiblePin


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.prompt_bindings')"))
    finally:
        await conn.close()


@pytest.fixture()
async def scene() -> AsyncIterator[dict]:
    """org + a concierge agent_instance (via pack → binding → instance)."""
    if not await _db_ready():
        pytest.skip("Postgres/migration 010 not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    slug = f"jw-{org.hex[:8]}"
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'P')", org)
        pack = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature) "
            "VALUES ($1,'1','1','{}','u','s') RETURNING id",
            slug,
        )
        arch = await conn.fetchval("SELECT id FROM agent_archetypes WHERE slug='concierge'")
        binding = await conn.fetchval(
            "INSERT INTO agent_bindings (pack_id, archetype_id, persona_default, tool_grants, "
            "kpi_defs, tier_defaults) VALUES ($1,$2,'Priya','{}','{}','{}') RETURNING id",
            pack, arch,
        )
        instance = await conn.fetchval(
            "INSERT INTO agent_instances (org_id, binding_id, persona_name, permission_manifest) "
            "VALUES ($1,$2,'Priya','{}') RETURNING id",
            org, binding,
        )
    finally:
        await conn.close()
    yield {"org": org, "instance": instance}
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)  # cascades instances
        await conn.execute("DELETE FROM prompt_layers WHERE task LIKE 'reply%'")
        await conn.execute(
            "DELETE FROM agent_bindings WHERE pack_id IN (SELECT id FROM packs WHERE slug=$1)", slug
        )
        await conn.execute("DELETE FROM packs WHERE slug=$1", slug)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_incompatible_pin_refused_and_compatible_pin_succeeds(scene: dict) -> None:
    org, instance = scene["org"], scene["instance"]
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        base = await registry.create_layer(
            s, layer_type="base", archetype="concierge", task="reply", version="1", content="B"
        )
        vertical_ok = await registry.create_layer(
            s, layer_type="vertical", archetype="concierge", task="reply", version="1",
            content="V", requires={"base": "1"},
        )
        vertical_bad = await registry.create_layer(
            s, layer_type="vertical", archetype="concierge", task="reply", version="9",
            content="V9", requires={"base": "2"},  # needs base v2, but base is v1
        )
        await s.commit()

    # Incompatible → refused.
    async with factory() as s:
        with pytest.raises(IncompatiblePin, match="requires base"):
            await registry.pin_binding(
                s, org_id=org, agent_instance_id=instance, task="reply",
                base_layer=base, vertical_layer=vertical_bad,
            )
    # Compatible → pinned + active.
    async with factory() as s:
        bid = await registry.pin_binding(
            s, org_id=org, agent_instance_id=instance, task="reply",
            base_layer=base, vertical_layer=vertical_ok,
        )
        await s.commit()
    async with factory() as s:
        assert await registry.get_active_binding(s, org, instance, "reply") == bid


async def test_one_active_binding_per_instance_task(scene: dict) -> None:
    org, instance = scene["org"], scene["instance"]
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        base1 = await registry.create_layer(
            s, layer_type="base", archetype="concierge", task="reply2", version="1", content="B1"
        )
        base2 = await registry.create_layer(
            s, layer_type="base", archetype="concierge", task="reply2", version="2", content="B2"
        )
        await s.commit()

    async with factory() as s:
        b1 = await registry.pin_binding(
            s, org_id=org, agent_instance_id=instance, task="reply2", base_layer=base1
        )
        await s.commit()
    async with factory() as s:
        b2 = await registry.pin_binding(
            s, org_id=org, agent_instance_id=instance, task="reply2", base_layer=base2
        )
        await s.commit()

    assert b1 != b2
    async with factory() as s:
        assert await registry.get_active_binding(s, org, instance, "reply2") == b2

    conn = await asyncpg.connect(_dsn())
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM prompt_bindings WHERE agent_instance_id=$1 AND task='reply2' "
            "AND active",
            instance,
        )
    finally:
        await conn.close()
    assert n == 1  # exactly one active


async def test_layer_content_is_immutable(scene: dict) -> None:
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        layer = await registry.create_layer(
            s, layer_type="base", archetype="concierge", task="reply3", version="1",
            content="frozen",
        )
        await s.commit()

    conn = await asyncpg.connect(_dsn())
    try:
        # Changing content is blocked by the trigger (even for the owner)…
        with pytest.raises(asyncpg.PostgresError, match="immutable"):
            await conn.execute("UPDATE prompt_layers SET content='x' WHERE id=$1", layer)
        # …but a status transition is allowed.
        await conn.execute("UPDATE prompt_layers SET status='deprecated' WHERE id=$1", layer)
    finally:
        await conn.close()
