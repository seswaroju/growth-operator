"""Tenant isolation for the pricing + approvals tables (audit follow-up) — probed as `app_rw`.

Seeds two orgs as the RLS-exempt owner, then connects as the non-bypass `app_rw` role and checks:
`quotes` and `committed_figures_ledger` behave like every other org-scoped table (own rows only;
zero without context). `approval_policies` is the special mixed-scope table — an org sees the
**global** rows plus its **own** tenant rows, never another org's; without context only globals
are visible. Closes the isolation-coverage gap flagged in the batch audit. Skips when DB down.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common.config import get_settings

STD_TABLES = ("quotes", "committed_figures_ledger")
GLOBAL_DESC = "GLOBAL"
ACTION = "iso.action"


def _owner_dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


def _app_rw_dsn() -> str:
    return get_settings().database_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_owner_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.approval_policies')"))
    finally:
        await conn.close()


async def _seed_org(conn: asyncpg.Connection, org: uuid.UUID) -> uuid.UUID:
    await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'iso')", org)
    pack = await conn.fetchval(
        "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, status) "
        "VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id", f"iso{org.hex[:8]}",
    )
    sid = await conn.fetchval(
        "INSERT INTO pricing_strategies (strategy_key, pack_id, engine, rule_schema, input_schema, "
        " rules) VALUES ($1,$2,'rules_v1','{}'::jsonb,'{}'::jsonb,'{}'::jsonb) RETURNING id",
        f"iso{org.hex[:8]}", pack,
    )
    await conn.execute(
        "INSERT INTO quotes (org_id, strategy_id, rules_version, inputs, breakdown, total_minor) "
        "VALUES ($1,$2,1,'{}'::jsonb,'[]'::jsonb,100)", org, sid,
    )
    await conn.execute(
        "INSERT INTO committed_figures_ledger (org_id, figure_type, amount_minor, source_ref) "
        "VALUES ($1,'total',100, gen_random_uuid())", org,
    )
    await conn.execute(
        "INSERT INTO approval_policies (scope, org_id, action_type, tier, description) "
        "VALUES ('tenant',$1,$2,3,$3)", org, ACTION, str(org),
    )
    return pack


@pytest.fixture()
async def two_orgs() -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    if not await _db_ready():
        pytest.skip("Postgres/migrations 013+014 not ready")
    a, b = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_owner_dsn())
    try:
        pack_a = await _seed_org(conn, a)
        pack_b = await _seed_org(conn, b)
        # one global (core-scope, org_id NULL) policy shared by all tenants
        await conn.execute(
            "INSERT INTO approval_policies (scope, action_type, tier, description) "
            "VALUES ('core',$1,4,$2)", ACTION, GLOBAL_DESC,
        )
    finally:
        await conn.close()
    yield a, b
    conn = await asyncpg.connect(_owner_dsn())
    try:
        await conn.execute("DELETE FROM approval_policies WHERE action_type = $1", ACTION)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", [a, b])
        await conn.execute("DELETE FROM pricing_strategies WHERE pack_id = ANY($1::uuid[])",
                           [pack_a, pack_b])
        await conn.execute("DELETE FROM packs WHERE id = ANY($1::uuid[])", [pack_a, pack_b])
    finally:
        await conn.close()


async def test_quotes_and_ledger_isolated_under_app_rw(
    two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, _b = two_orgs
    conn = await asyncpg.connect(_app_rw_dsn())  # non-bypass app role
    try:
        for table in STD_TABLES:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.org_id', $1, true)", str(a))
                orgs = [r["org_id"] for r in await conn.fetch(f"SELECT org_id FROM {table}")]
            assert orgs and all(o == a for o in orgs), f"{table} leaked cross-tenant: {orgs}"

            async with conn.transaction():  # no context → fail closed
                n = await conn.fetchval(f"SELECT count(*) FROM {table}")
            assert n == 0, f"{table} not fail-closed without context (saw {n})"
    finally:
        await conn.close()


async def test_approval_policies_shows_globals_plus_own_only(
    two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_orgs
    conn = await asyncpg.connect(_app_rw_dsn())
    try:
        # Org A's context: global + A's own tenant rule, never B's.
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.org_id', $1, true)", str(a))
            descs = {
                r["description"] for r in await conn.fetch(
                    "SELECT description FROM approval_policies WHERE action_type = $1", ACTION)
            }
        assert GLOBAL_DESC in descs
        assert str(a) in descs
        assert str(b) not in descs, f"org A saw org B's tenant policy: {descs}"

        # No context: only global rows are visible (tenant rows fail closed).
        async with conn.transaction():
            descs_none = {
                r["description"] for r in await conn.fetch(
                    "SELECT description FROM approval_policies WHERE action_type = $1", ACTION)
            }
        # without context: only global rows visible (tenant rows fail closed)
        assert descs_none == {GLOBAL_DESC}, descs_none
    finally:
        await conn.close()
