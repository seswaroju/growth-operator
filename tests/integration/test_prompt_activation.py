"""Prompt activation pipeline + executor→composer wiring against real Postgres.

Installing a pack now **pins** a base+vertical+tenant prompt binding per concierge (instance, task),
so the executor composes a **grounded** prompt (the real layered persona/safety/domain content)
instead of the MVP-055 skeleton. Archetypes with no base layer are skipped (skeleton fallback), and
composition never blocks a run — a missing binding falls back to the skeleton. Skips if DB is down.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.packs.installer import install
from core.runtime.executor import _make_compose

_JEWELRY = Path(__file__).resolve().parents[2] / "verticals" / "jewelry"


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


class Scene:
    def __init__(self, org: uuid.UUID, pack_id: uuid.UUID, concierge: uuid.UUID) -> None:
        self.org = org
        self.pack_id = pack_id
        self.concierge = concierge


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/prompt_bindings not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, vertical) VALUES ($1,'PA','jewelry')", org)
    finally:
        await conn.close()
    await install(org, _JEWELRY)
    conn = await asyncpg.connect(_dsn())
    try:
        pid = await conn.fetchval("SELECT id FROM packs WHERE slug='jewelry'")
        concierge = await conn.fetchval(
            "SELECT i.id FROM agent_instances i JOIN agent_bindings b ON b.id=i.binding_id "
            "JOIN agent_archetypes a ON a.id=b.archetype_id "
            "WHERE i.org_id=$1 AND a.slug='concierge'", org)
    finally:
        await conn.close()
    yield Scene(org, pid, concierge)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)  # cascades bindings/layers
        # The jewelry pack's rows (prompt_layers, agent_bindings, approval_policies,
        # catalog_schemas, the pack itself) are SHARED across every org that installed it — remove
        # them ONLY when no other org still has the pack installed, else the delete FK-fails against
        # another org's bindings. Base prompt_layers (pack_id NULL) are shared infra, left intact.
        # (Mirrors the canonical teardown in tests/e2e/test_jewelry_install.py.)
        if pid and not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pack_installations WHERE pack_id=$1)", pid
        ):
            for t in ("prompt_layers", "approval_policies", "agent_bindings", "catalog_schemas"):
                await conn.execute(f"DELETE FROM {t} WHERE pack_id=$1", pid)
            await conn.execute("DELETE FROM packs WHERE id=$1", pid)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_install_pins_concierge_prompt_bindings(scene: Scene) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            "SELECT task, base_layer, vertical_layer, tenant_layer, active "
            "FROM prompt_bindings WHERE agent_instance_id=$1 ORDER BY task", scene.concierge)
    finally:
        await conn.close()
    tasks = {r["task"] for r in rows}
    assert tasks == {"qualify", "catalog_answer", "quote", "book_visit"}
    for r in rows:  # every binding pins all three layers and is active
        assert r["base_layer"] and r["vertical_layer"] and r["tenant_layer"] and r["active"]


async def test_archetype_without_base_layer_is_skipped(scene: Scene) -> None:
    # nurture has no prompts/base/nurture.md → activation skips it (no binding pinned).
    conn = await asyncpg.connect(_dsn())
    try:
        nurture = await conn.fetchval(
            "SELECT i.id FROM agent_instances i JOIN agent_bindings b ON b.id=i.binding_id "
            "JOIN agent_archetypes a ON a.id=b.archetype_id "
            "WHERE i.org_id=$1 AND a.slug='nurture'", scene.org)
        n = await conn.fetchval(
            "SELECT count(*) FROM prompt_bindings WHERE agent_instance_id=$1", nurture)
    finally:
        await conn.close()
    assert n == 0


async def test_executor_composes_grounded_prompt(scene: Scene) -> None:
    compose = _make_compose(scene.org, scene.concierge, "Priya")
    text, digest = await compose({"input": {"task": "catalog_answer", "body": "do you have this"}})
    assert "base.concierge" in text          # the real base layer content, not the skeleton
    assert not text.startswith("[persona:")
    assert len(digest) == 64                 # sha256 hex of the composed prompt
    # deterministic: same binding → same prompt/hash
    text2, digest2 = await compose({"input": {"task": "catalog_answer", "body": "different body"}})
    assert digest2 == digest


async def test_compose_falls_back_to_skeleton_without_binding(scene: Scene) -> None:
    compose = _make_compose(scene.org, scene.concierge, "Priya")
    text, _ = await compose({"input": {"task": "no_such_task"}})
    assert text.startswith("[persona:Priya]")   # skeleton fallback when no binding exists
