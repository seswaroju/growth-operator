"""LP-2d — the landing tools driven through the agent boundary, with the jewelry pack installed so
its seeded approval rule fires. Real Postgres; skips when the DB is down.

Proves: `landing_page.publish` is tier-2 (owner approval) per the seeded pack rule; the `generate`
tool drafts candidate variants autonomously; and `publish` only succeeds on an owner-approved page,
auditing the transition as an `agent` action.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from core.approvals.engine import evaluate_tool
from core.common import db as dbmod
from core.common.config import get_settings
from core.landing import lifecycle
from core.landing.lifecycle import InvalidTransition
from core.mediation.proxy import RunContext
from core.mediation.tools import REGISTRY
from core.packs import installer
from core.packs.bundle import parse_pack_dir
from core.packs.installer import _Ctx
from core.tenancy.middleware import org_scoped_session

_JEWELRY = Path(__file__).resolve().parents[2] / "verticals" / "jewelry"


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.landing_pages')"))
    finally:
        await conn.close()


class Scene:
    def __init__(self, org: uuid.UUID, pack_id: uuid.UUID) -> None:
        self.org = org
        self.pack_id = pack_id


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/landing not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'LP2D')", org)
        pack_id = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ('jewelry',$1,'>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"lp2d{org.hex[:8]}")
        await conn.execute(
            "INSERT INTO pack_installations (org_id, pack_id, status) VALUES ($1,$2,'active')",
            org, pack_id)
    finally:
        await conn.close()
    async with org_scoped_session(org) as s:  # seed the jewelry pack tier rules (incl. LP-2d)
        await installer._seed_policies(
            s, _Ctx(org_id=org, pack_id=pack_id, parsed=parse_pack_dir(_JEWELRY)))
        await s.commit()
    yield Scene(org, pack_id)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM approval_policies WHERE pack_id=$1", pack_id)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack_id)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _ctx(org: uuid.UUID) -> RunContext:
    return RunContext(org_id=org, run_id=uuid.uuid4(), instance_id=uuid.uuid4(),
                      manifest={}, manifest_hash="")


_GEN_PARAMS: dict[str, Any] = {
    "slug": "agent-diwali", "headline": "Everyday Diamond Pendants", "offer": "from ₹29,999",
    "products": [{"title": "Solitaire Pendant", "price_text": "₹29,999"},
                 {"title": "Halo Pendant", "price_text": "₹42,500"}], "variants": 3}


async def _generate(scene: Scene) -> str:
    async with org_scoped_session(scene.org) as s:
        out = await REGISTRY["landing_page.generate"](_ctx(scene.org), _GEN_PARAMS, s, uuid.uuid4())
        await s.commit()
    return out["page_id"]


async def _publish(scene: Scene, page_id: str) -> dict:
    async with org_scoped_session(scene.org) as s:
        out = await REGISTRY["landing_page.publish"](
            _ctx(scene.org), {"page_id": page_id}, s, uuid.uuid4())
        await s.commit()
    return out


async def test_publish_action_is_tier2_owner_approval(scene: Scene) -> None:
    # the seeded pack rule makes an agent publish need owner approval (tier 2)
    async with org_scoped_session(scene.org) as s:
        decision = await evaluate_tool(
            s, org_id=scene.org, actor_instance_id=None, untrusted=False,
            tool="landing_page.publish", params={})
    assert decision.tier == 2


async def test_agent_generate_drafts_then_publish_after_approval(scene: Scene) -> None:
    page_id = await _generate(scene)
    conn = await asyncpg.connect(_dsn())
    try:
        status = await conn.fetchval(
            "SELECT status FROM landing_pages WHERE id=$1::uuid", page_id)
        n = await conn.fetchval(
            "SELECT count(*) FROM landing_page_versions WHERE page_id=$1::uuid", page_id)
    finally:
        await conn.close()
    assert status == "generated" and n == 3  # agent drafted 3 candidates, not yet live

    # the owner approves + selects a variant (HITL #1), then the agent publishes
    async with org_scoped_session(scene.org) as s:
        assert await lifecycle.select_variant(
            s, scene.org, uuid.UUID(page_id), 2, actor_id=None) == "approved"
        await s.commit()
    assert (await _publish(scene, page_id))["status"] == "published"

    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval(
            "SELECT status FROM landing_pages WHERE id=$1::uuid", page_id) == "published"
        # the publish transition is audited as an AGENT action
        actor = await conn.fetchval(
            "SELECT actor_type FROM audit_log WHERE resource=$1 "
            "AND action='landing_page.transition' AND payload->>'to'='published'", page_id)
        assert actor == "agent"
    finally:
        await conn.close()


async def test_agent_cannot_publish_an_unapproved_page(scene: Scene) -> None:
    page_id = await _generate(scene)  # still 'generated' (owner hasn't approved a variant)
    with pytest.raises(InvalidTransition):
        await _publish(scene, page_id)
    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval(
            "SELECT status FROM landing_pages WHERE id=$1::uuid", page_id) == "generated"
    finally:
        await conn.close()
