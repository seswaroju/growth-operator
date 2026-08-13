"""Entitlement resolution must fail closed and never leak across tenants (PLAN-2).

`billing_subscriptions`, `pack_installations` and `agent_instances` are all FORCE-RLS, so the
resolver's correctness depends on running under the right tenant context. These tests verify the
*deny* direction: no context and wrong context must never over-permit.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from sqlalchemy import text

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.entitlements import resolve

STRUCTURED = {"entitlement_schema_version": 1,
              "entitlements": ["catalog", "landing_pages"], "channels": ["whatsapp"]}


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.billing_subscriptions')"))
    finally:
        await conn.close()


class Pair:
    def __init__(self, a: uuid.UUID, b: uuid.UUID, plan: uuid.UUID) -> None:
        self.a, self.b, self.plan = a, b, plan


@pytest.fixture()
async def pair() -> AsyncIterator[Pair]:
    if not await _db_ready():
        pytest.skip("Postgres/billing not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    tag = f"iso-{uuid.uuid4().hex[:8]}"
    conn = await asyncpg.connect(_dsn())
    a, b, plan = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        for oid, n in ((a, "A"), (b, "B")):
            await conn.execute(
                "INSERT INTO organizations (id, name, vertical) VALUES ($1,$2,'jewelry')",
                oid, f"{tag}-{n}")
        await conn.execute(
            "INSERT INTO billing_plans (id, name, price_minor, features, config, max_managers, "
            "max_staff) VALUES ($1,$2,0,'[]'::jsonb,$3::jsonb,1,1)",
            plan, f"{tag}-plan", json.dumps(STRUCTURED))
        # Only org A subscribes. Org B must never see A's plan, promotions or state.
        await conn.execute(
            "INSERT INTO billing_subscriptions (org_id, plan_id, status) VALUES ($1,$2,'active')",
            a, plan)
        yield Pair(a, b, plan)
    finally:
        for oid in (a, b):
            await conn.execute("DELETE FROM billing_subscriptions WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        await conn.execute("DELETE FROM billing_plans WHERE id=$1", plan)
        await conn.close()
        # Dispose, don't just drop the cache: clearing the lru_cache alone orphans a pool whose
        # connections belong to this test's event loop, and the next file to build an engine trips
        # over them during GC ("Event loop is closed").
        await dbmod.get_engine().dispose()
        dbmod.get_engine.cache_clear()
        dbmod.get_sessionmaker.cache_clear()


async def test_org_b_cannot_see_org_as_plan_or_capabilities(pair: Pair) -> None:
    async with dbmod.get_sessionmaker()() as s:
        eff_a = await resolve(s, pair.a)
    async with dbmod.get_sessionmaker()() as s:
        eff_b = await resolve(s, pair.b)

    assert eff_a.capabilities == frozenset({"catalog", "landing_pages"})
    assert eff_a.plan_id == pair.plan

    assert eff_b.capabilities == frozenset()
    assert eff_b.plan_id is None
    assert eff_b.plan_name is None
    assert eff_b.channels == frozenset()
    assert eff_b.subscription_state == "none"   # not even "cancelled" — B has no history


async def test_resolution_without_tenant_context_fails_closed(pair: Pair) -> None:
    """The resolver sets context itself; this proves the underlying read is genuinely RLS-guarded,
    so a future refactor that drops the `set_org_context` call cannot silently over-permit."""
    async with dbmod.get_sessionmaker()() as s:
        rows = (
            await s.execute(text("SELECT count(*) FROM billing_subscriptions"))
        ).scalar_one()
    assert rows == 0


async def test_a_wrong_tenant_context_cannot_read_another_orgs_subscription(pair: Pair) -> None:
    from core.tenancy.repository import set_org_context

    async with dbmod.get_sessionmaker()() as s:
        await set_org_context(s, pair.b)
        seen = (
            await s.execute(
                text("SELECT count(*) FROM billing_subscriptions WHERE org_id = :a"),
                {"a": str(pair.a)})
        ).scalar_one()
    assert seen == 0


async def test_resolving_b_after_a_in_the_same_session_does_not_inherit_as_grants(
    pair: Pair,
) -> None:
    """Context is transaction-local; a second resolve in the same session must re-scope cleanly."""
    async with dbmod.get_sessionmaker()() as s:
        eff_a = await resolve(s, pair.a)
        eff_b = await resolve(s, pair.b)
    assert eff_a.capabilities == frozenset({"catalog", "landing_pages"})
    assert eff_b.capabilities == frozenset()
