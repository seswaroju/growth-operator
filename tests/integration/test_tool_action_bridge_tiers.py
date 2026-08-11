"""Tool→action bridge tiering end-to-end (BLOCKERS #20) — real Postgres + seeded jewelry rules.

Proves the seeded MVP-044 pack policies now *fire* for a tool call: a plain reply auto-sends
(tier 1), a high-value quote and a discounted quote need approval (tier 2), a small no-discount
quote auto-sends (tier 1), and a broadcast always confirms (tier 3). Skips when the DB is down.
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
        return bool(await conn.fetchval("SELECT to_regclass('public.approval_policies')"))
    finally:
        await conn.close()


class Scene:
    def __init__(self, org: uuid.UUID, pack_id: uuid.UUID) -> None:
        self.org = org
        self.pack_id = pack_id


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/approval_policies not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'TB')", org)
        # Disable quiet hours (empty window) so the auto-send tier assertions are clock-independent.
        await conn.execute(
            "INSERT INTO tenant_settings (org_id, key, value, schema_ref, version) VALUES "
            "($1,'quiet_hours.start','\"00:00\"'::jsonb,'core.time',1),"
            "($1,'quiet_hours.end','\"00:00\"'::jsonb,'core.time',1)", org)
        pack_id = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ('jewelry',$1,'>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"tb{org.hex[:8]}")
        await conn.execute(
            "INSERT INTO pack_installations (org_id, pack_id, status) VALUES ($1,$2,'active')",
            org, pack_id)  # install so the pack's rules apply (per-pack scoping, #22)
    finally:
        await conn.close()
    async with org_scoped_session(org) as s:  # seed the jewelry pack tier rules (global)
        await installer._seed_policies(
            s, _Ctx(org_id=org, pack_id=pack_id, parsed=parse_pack_dir(_JEWELRY)))
        await s.commit()
    yield Scene(org, pack_id)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM approval_policies WHERE pack_id=$1", pack_id)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack_id)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _tier(scene: Scene, tool: str, params: dict[str, Any]) -> int:
    async with org_scoped_session(scene.org) as s:
        d = await evaluate_tool(s, org_id=scene.org, actor_instance_id=None, untrusted=False,
                                tool=tool, params=params)
    return d.tier


async def test_plain_reply_auto_sends_tier1(scene: Scene) -> None:
    assert await _tier(scene, "messages.send", {"body": "Yes, we're open Mon-Sat till 8pm"}) == 1


async def test_high_value_quote_needs_approval_tier2(scene: Scene) -> None:
    # ₹1,50,000 ≥ the pack's ₹1,00,000 threshold → the high-value-quote rule fires.
    assert await _tier(scene, "messages.send", {"body": "This necklace is ₹1,50,000"}) == 2


async def test_small_quote_without_discount_auto_sends_tier1(scene: Scene) -> None:
    # ₹80,000 < ₹1,00,000 and no discount → only the plain-reply (tier 1) rule matches.
    assert await _tier(scene, "messages.send", {"body": "This ring is ₹80,000"}) == 1


async def test_discounted_quote_needs_approval_tier2(scene: Scene) -> None:
    assert await _tier(
        scene, "messages.send", {"body": "This ring is ₹80,000", "discount_minor": 400000}) == 2


async def test_broadcast_always_confirms_tier3(scene: Scene) -> None:
    assert await _tier(scene, "campaigns.execute", {"recipients": ["+91a", "+91b"]}) == 3
