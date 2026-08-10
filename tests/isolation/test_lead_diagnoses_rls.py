"""Tenant isolation for `lead_diagnoses` (MVP-073j) — probed as the non-bypass `app_rw` role.

Seeds a diagnosis label for two orgs as the owner, then connects as `app_rw`: an org sees only its
own labels, and without tenant context the table fails closed (zero rows). Skips when DB is down.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common.config import get_settings


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
        return bool(await conn.fetchval("SELECT to_regclass('public.lead_diagnoses')"))
    finally:
        await conn.close()


async def _seed(conn: asyncpg.Connection, org: uuid.UUID) -> None:
    await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'iso')", org)
    contact = await conn.fetchval(
        "INSERT INTO contacts (id, org_id) VALUES (gen_random_uuid(),$1) RETURNING id", org)
    lead = await conn.fetchval(
        "INSERT INTO leads (org_id, contact_id) VALUES ($1,$2) RETURNING id", org, contact)
    await conn.execute(
        "INSERT INTO lead_diagnoses (org_id, lead_id, top_reason, recommended_action_id) "
        "VALUES ($1,$2,'sticker_shock','act_value_reframe')", org, lead)


@pytest.fixture()
async def two_orgs() -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    if not await _db_ready():
        pytest.skip("Postgres/lead_diagnoses not ready")
    a, b = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_owner_dsn())
    try:
        for org in (a, b):
            await _seed(conn, org)
    finally:
        await conn.close()
    yield a, b
    conn = await asyncpg.connect(_owner_dsn())
    try:
        for t in ("lead_diagnoses", "leads", "contacts", "organizations"):
            col = "id" if t == "organizations" else "org_id"
            await conn.execute(f"DELETE FROM {t} WHERE {col} = ANY($1::uuid[])", [a, b])
    finally:
        await conn.close()


async def test_lead_diagnoses_isolated_under_app_rw(
    two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_orgs
    conn = await asyncpg.connect(_app_rw_dsn())  # non-bypass app role
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.org_id', $1, true)", str(a))
            orgs = [r["org_id"] for r in await conn.fetch("SELECT org_id FROM lead_diagnoses")]
        assert orgs and all(o == a for o in orgs), f"lead_diagnoses leaked cross-tenant: {orgs}"
        assert b not in orgs

        async with conn.transaction():  # no context → fail closed
            n = await conn.fetchval("SELECT count(*) FROM lead_diagnoses")
        assert n == 0, f"lead_diagnoses not fail-closed without context (saw {n})"
    finally:
        await conn.close()
