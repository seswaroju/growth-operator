"""Prompt composer + tenant-layer generator (MVP-059).

render_template strictness is unit-tested; against real Postgres a base+vertical+tenant binding
composes into a hash-stamped prompt that is reproducible across a cache clear ("processes"), a
missing parameter or layer fails closed, and the tenant-layer generator bakes settings
idempotently. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.prompts import composer, tenant_layer
from core.prompts.composer import LayerMissing, MissingParam, render_template
from core.tenancy.middleware import org_scoped_session

# ---- Unit: strict template rendering --------------------------------------------------


def test_render_template_fills_and_is_strict() -> None:
    filled = render_template("Hi {name} at {store}", {"name": "Priya", "store": "GJ"})
    assert filled == "Hi Priya at GJ"
    with pytest.raises(MissingParam) as ei:
        render_template("Hi {name}", {})
    assert ei.value.name == "name"


def test_render_template_no_placeholders_is_identity() -> None:
    assert render_template("no params here", {"x": 1}) == "no params here"


# ---- Integration ----------------------------------------------------------------------


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
    if not await _db_ready():
        pytest.skip("Postgres/prompt_bindings (010) not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    composer.clear_cache()
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'PC')", org)
        pack_id = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"pc{org.hex[:8]}",
        )
        arch_id = await conn.fetchval("SELECT id FROM agent_archetypes WHERE slug='concierge'")
        binding_id = await conn.fetchval(
            "INSERT INTO agent_bindings (pack_id, archetype_id, persona_default, tool_grants, "
            "kpi_defs, tier_defaults) VALUES ($1,$2,'P','[]'::jsonb,'{}'::jsonb,'[]'::jsonb) "
            "RETURNING id",
            pack_id, arch_id,
        )
        instance_id = await conn.fetchval(
            "INSERT INTO agent_instances (org_id, binding_id, persona_name, permission_manifest) "
            "VALUES ($1,$2,'P','{}'::jsonb) RETURNING id",
            org, binding_id,
        )
        base = await conn.fetchval(
            "INSERT INTO prompt_layers (layer_type, archetype, task, version, content, status) "
            "VALUES ('base','concierge','qualify','1.0','BASE RULES','active') RETURNING id"
        )
        vertical = await conn.fetchval(
            "INSERT INTO prompt_layers (layer_type, pack_id, archetype, task, version, content, "
            "requires, status) VALUES ('vertical',$1,'concierge','qualify','3.2','VERTICAL FLOW',"
            "'{\"base\":\">=1.0\"}'::jsonb,'active') RETURNING id",
            pack_id,
        )
        tenant = await conn.fetchval(
            "INSERT INTO prompt_layers (layer_type, org_id, archetype, task, version, content, "
            "status) VALUES ('tenant',$1,'concierge','qualify','1.a','You are {persona_name}.',"
            "'candidate') RETURNING id",
            org,
        )
        pb = await conn.fetchval(
            "INSERT INTO prompt_bindings (org_id, agent_instance_id, task, base_layer, "
            "vertical_layer, tenant_layer, active) VALUES ($1,$2,'qualify',$3,$4,$5,true) "
            "RETURNING id",
            org, instance_id, base, vertical, tenant,
        )
    finally:
        await conn.close()
    yield {"org": org, "binding": pb, "pack_id": pack_id}
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM prompt_bindings WHERE org_id=$1", org)
        await conn.execute("DELETE FROM prompt_layers WHERE org_id=$1 OR pack_id=$2", org, pack_id)
        await conn.execute("DELETE FROM prompt_layers WHERE layer_type='base' AND task='qualify'")
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)  # cascades instance
        await conn.execute("DELETE FROM agent_bindings WHERE pack_id=$1", pack_id)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack_id)
    finally:
        await conn.close()
    composer.clear_cache()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_compose_stacks_layers_and_hashes(scene: dict) -> None:
    async with org_scoped_session(scene["org"]) as s:
        cp = await composer.render(s, scene["org"], scene["binding"], {"persona_name": "Priya"})
    assert cp.text == "BASE RULES\n\nVERTICAL FLOW\n\nYou are Priya."
    assert cp.layer_versions == {"base": "1.0", "vertical": "3.2", "tenant": "1.a"}
    assert len(cp.content_hash) == 64


async def test_hash_reproducible_across_cache_clear(scene: dict) -> None:
    async with org_scoped_session(scene["org"]) as s:
        first = await composer.render(s, scene["org"], scene["binding"], {"persona_name": "Priya"})
    composer.clear_cache()  # simulate a fresh process (empty layer cache)
    async with org_scoped_session(scene["org"]) as s:
        second = await composer.render(s, scene["org"], scene["binding"], {"persona_name": "Priya"})
    assert first.content_hash == second.content_hash


async def test_missing_param_refuses(scene: dict) -> None:
    async with org_scoped_session(scene["org"]) as s:
        with pytest.raises(MissingParam):
            await composer.render(s, scene["org"], scene["binding"], {})  # persona_name absent


async def test_unknown_binding_fails_closed(scene: dict) -> None:
    async with org_scoped_session(scene["org"]) as s:
        with pytest.raises(LayerMissing):
            await composer.render(s, scene["org"], uuid.uuid4(), {})


async def test_generate_tenant_layer_is_idempotent_and_bakes(scene: dict) -> None:
    org = scene["org"]
    facts = {"persona_name": "Anu", "store_name": "GJ", "store_facts": "f",
             "policies": "p", "language_mix": "Hinglish"}
    async with org_scoped_session(org) as s:
        gen = tenant_layer.generate_tenant_layer
        first = await gen(s, org, "concierge", "qualify", facts=facts)
        same = await gen(s, org, "concierge", "qualify", facts=facts)
        changed = await tenant_layer.generate_tenant_layer(
            s, org, "concierge", "qualify", facts={**facts, "persona_name": "Zara"}
        )
    assert first == same and first != changed  # same facts dedupe; changed facts → new version

    conn = await asyncpg.connect(_dsn())
    try:
        content = await conn.fetchval("SELECT content FROM prompt_layers WHERE id=$1", first)
    finally:
        await conn.close()
    assert "Anu" in content and "{persona_name}" not in content  # settings baked in


async def test_resolve_tenant_facts_defaults(scene: dict) -> None:
    async with org_scoped_session(scene["org"]) as s:
        facts = await tenant_layer.resolve_tenant_facts(s, scene["org"])
    assert facts["persona_name"] == "our assistant" and facts["store_name"] == "the store"
