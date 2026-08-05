"""Planner consumer path (MVP-056) against real Postgres — msg.received -> routed run enqueue.

Drives `planner._handle` with a captured `start_run` (so no full agent run is executed): a normal
inbound message enqueues a run against the org's active concierge instance with the classified
intent+task; a paused tenant, a fully-suppressed contact, and no active instance each drop the
message with the right outcome. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.runtime import planner


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
    async def get(self, key: str) -> Any:
        return None

    async def incr(self, key: str) -> int:
        return 1

    async def expire(self, key: str, secs: int) -> bool:
        return True


class Scene:
    def __init__(self, org: uuid.UUID, contact: uuid.UUID, instance: uuid.UUID) -> None:
        self.org = org
        self.contact = contact
        self.instance = instance
        self.calls: list[dict[str, Any]] = []

    async def capture_start_run(self, org_id: uuid.UUID, instance_id: uuid.UUID, **kw: Any) -> Any:
        self.calls.append({"org": org_id, "instance": instance_id, **kw})

    def envelope(self, body: str) -> dict[str, Any]:
        return {"subject": str(self.org), "data": {
            "conversation_id": str(uuid.uuid4()), "contact_id": str(self.contact), "body": body}}


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/runtime not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, vertical, status) "
            "VALUES ($1,'PL','jewelry','active')", org)
        # slug MUST be 'jewelry' so the taxonomy loads from verticals/jewelry (version is unique)
        pack = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ('jewelry',$1,'>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"t{org.hex[:8]}")
        arch = await conn.fetchval("SELECT id FROM agent_archetypes WHERE slug='concierge'")
        binding = await conn.fetchval(
            "INSERT INTO agent_bindings (pack_id, archetype_id, persona_default, tool_grants, "
            " kpi_defs, tier_defaults) VALUES ($1,$2,'Priya','{}'::jsonb,'{}'::jsonb,'{}'::jsonb) "
            "RETURNING id", pack, arch)
        instance = await conn.fetchval(
            "INSERT INTO agent_instances (org_id, binding_id, persona_name, status, "
            " permission_manifest, budget_caps) "
            "VALUES ($1,$2,'Priya','active','{}'::jsonb,'{}'::jsonb) RETURNING id", org, binding)
        contact = await conn.fetchval(
            "INSERT INTO contacts (org_id, phone) VALUES ($1,'+910000000001') RETURNING id", org)
    finally:
        await conn.close()
    yield Scene(org, contact, instance)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM suppressions WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_instances WHERE org_id=$1", org)
        await conn.execute("DELETE FROM agent_bindings WHERE pack_id=$1", pack)
        await conn.execute("DELETE FROM contacts WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _run(scene: Scene, body: str) -> str:
    return await planner._handle(
        scene.envelope(body), redis=FakeRedis(), start_run_fn=scene.capture_start_run)


async def test_inbound_message_enqueues_run_to_concierge(scene: Scene) -> None:
    outcome = await _run(scene, "Do you have this in stock?")
    assert outcome == "enqueued"
    assert len(scene.calls) == 1
    call = scene.calls[0]
    assert call["instance"] == scene.instance                 # the active concierge instance
    assert call["trigger"] == "msg.received"
    assert call["input"]["intent"] == "availability"
    assert call["input"]["task"] == "catalog_answer"
    assert call["input"]["clarify"] is False


async def test_unclassifiable_still_enqueues_concierge_clarify(scene: Scene) -> None:
    outcome = await _run(scene, "zzzz qwerty nonsense")
    assert outcome == "enqueued"
    assert scene.calls[0]["input"]["task"] == "qualify"
    assert scene.calls[0]["input"]["clarify"] is True         # fallback flagged for the concierge


async def test_paused_tenant_drops(scene: Scene) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("UPDATE organizations SET status='paused' WHERE id=$1", scene.org)
    finally:
        await conn.close()
    assert await _run(scene, "hello") == "paused"
    assert scene.calls == []                                  # nothing enqueued


async def test_fully_suppressed_contact_drops(scene: Scene) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO suppressions (org_id, contact_id, scope) VALUES ($1,$2,'all')",
            scene.org, scene.contact)
    finally:
        await conn.close()
    assert await _run(scene, "Do you have this in stock?") == "suppressed"
    assert scene.calls == []


async def test_no_active_instance_drops(scene: Scene) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("UPDATE agent_instances SET status='paused' WHERE id=$1", scene.instance)
    finally:
        await conn.close()
    assert await _run(scene, "Do you have this in stock?") == "no_instance"
    assert scene.calls == []
