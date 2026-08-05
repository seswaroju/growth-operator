"""Pack policy seeding (MVP-044) against real Postgres — the `_seed_policies` installer step.

Drives `_seed_policies` directly (as `app_rw`, under org context) on the parsed jewelry pack and
proves the acceptance: the seeded `approval_policies` (scope='pack') match the pack's binding
`tier_defaults` **exactly** (diff = ∅), a re-seed is idempotent, the domain fields map correctly
(`30m`→1800s, `hold_and_remind`→`hold`, approver→chain), and the RLS added for pack seeding is
tight — `app_rw` can seed a `scope='pack'` global row but **not** a `scope='core'` one. Skips when
the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

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
        self.parsed = parse_pack_dir(_JEWELRY)


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/approval_policies not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'PS')", org)
        pack_id = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ('jewelry',$1,'>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"ps{org.hex[:8]}")
    finally:
        await conn.close()
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


async def _seed(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        await installer._seed_policies(s, _Ctx(org_id=scene.org, pack_id=scene.pack_id,
                                               parsed=scene.parsed))
        await s.commit()


def _pack_rules(scene: Scene) -> set[tuple[str, int, str, str]]:
    return {
        (r.applies_to, r.tier, r.condition, r.description or r.rule_key)
        for b in scene.parsed.bindings.bindings for r in b.tier_defaults
    }


async def _seeded_rows(scene: Scene) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(_dsn())
    try:
        return list(await conn.fetch(
            "SELECT * FROM approval_policies WHERE pack_id=$1 AND scope='pack'", scene.pack_id))
    finally:
        await conn.close()


async def test_seeded_policies_match_pack_tier_defaults_exactly(scene: Scene) -> None:
    await _seed(scene)
    rows = await _seeded_rows(scene)
    seeded = {(r["action_type"], r["tier"], r["cel_expr"], r["description"]) for r in rows}
    assert seeded == _pack_rules(scene)   # diff = ∅ (the AC)


async def test_reseed_is_idempotent(scene: Scene) -> None:
    await _seed(scene)
    n1 = len(await _seeded_rows(scene))
    await _seed(scene)                     # second seed of the same pack
    n2 = len(await _seeded_rows(scene))
    assert n1 == n2 == len(_pack_rules(scene))   # no duplicates


async def test_domain_field_mapping(scene: Scene) -> None:
    await _seed(scene)
    rows = {r["description"]: r for r in await _seeded_rows(scene)}
    hv = rows["Sending quotes of ₹1,00,000 or more"]
    assert hv["tier"] == 2
    assert hv["timeout_s"] == 1800                       # 30m -> 1800s
    assert hv["on_timeout"] == "hold"                    # hold_and_remind -> hold (DB CHECK)
    assert hv["approver_chain"] == '["role:owner"]'      # jsonb text form
    reply = rows["Replies in an active customer chat"]
    assert reply["tier"] == 1 and reply["cel_expr"] == "true"


async def test_app_rw_can_seed_pack_but_not_core_scope(scene: Scene) -> None:
    await _seed(scene)                                    # pack rows seeded fine (proven above)
    # The pack-insert RLS is tight: app_rw may NOT forge a global scope='core' rule.
    with pytest.raises(Exception):  # noqa: B017,PT011 - RLS raises InsufficientPrivilege
        async with org_scoped_session(scene.org) as s:
            from sqlalchemy import text
            await s.execute(
                text("INSERT INTO approval_policies (scope, action_type, tier, description) "
                     "VALUES ('core','payment.charge',4,'forged')"))
            await s.commit()
