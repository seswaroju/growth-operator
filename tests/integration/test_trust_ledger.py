"""Trust ledger settle + incidents (MVP-070) against real Postgres.

Covers ap-11 (demotion offers are digest-only, never auto-applied) and ap-12 (an incident resets
the counter and writes a self-expiring 14-day tightening row), plus the 72h clean-window boundary
(an incident at 71h59m blocks the increment) and settle idempotency. Skips when DB unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from core.approvals import trust
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.middleware import org_scoped_session

ACTION = "messages.send"


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='approvals' AND column_name='trust_settled'"))
    finally:
        await conn.close()


@pytest.fixture()
async def org() -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres/approvals trust_settled (MVP-070) not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org_id = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'T')", org_id)
    finally:
        await conn.close()
    yield org_id
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM approvals WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM trust_ledger WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM incident_tightening WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org_id)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _approved(org: uuid.UUID, *, decided_hours_ago: float, action: str = ACTION) -> uuid.UUID:
    now = datetime.now(UTC)
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(
            "INSERT INTO approvals (org_id, action_type, tier, payload, status, decided_at, "
            " expires_at) VALUES ($1,$2,2,'{}'::jsonb,'approved',$3,$4) RETURNING id",
            org, action, now - timedelta(hours=decided_hours_ago), now)
    finally:
        await conn.close()


async def _clean(org: uuid.UUID, action: str = ACTION) -> int:
    conn = await asyncpg.connect(_dsn())
    try:
        v = await conn.fetchval(
            "SELECT clean_approvals FROM trust_ledger WHERE org_id=$1 AND action_type=$2",
            org, action)
        return v or 0
    finally:
        await conn.close()


async def test_settle_increments_a_72h_clean_approval(org: uuid.UUID) -> None:
    await _approved(org, decided_hours_ago=73)
    async with org_scoped_session(org) as s:
        n = await trust.settle(s, org)
        await s.commit()
    assert n == 1 and await _clean(org) == 1


async def test_settle_is_idempotent(org: uuid.UUID) -> None:
    await _approved(org, decided_hours_ago=73)
    async with org_scoped_session(org) as s:
        await trust.settle(s, org)
        await s.commit()
    async with org_scoped_session(org) as s:
        again = await trust.settle(s, org)  # already settled → no re-count
        await s.commit()
    assert again == 0 and await _clean(org) == 1


async def test_settle_skips_inside_the_window(org: uuid.UUID) -> None:
    await _approved(org, decided_hours_ago=10)  # window not yet passed
    async with org_scoped_session(org) as s:
        n = await trust.settle(s, org)
        await s.commit()
    assert n == 0 and await _clean(org) == 0


async def test_incident_resets_and_writes_14d_tightening(org: uuid.UUID) -> None:
    # earn some trust first
    await _approved(org, decided_hours_ago=73)
    async with org_scoped_session(org) as s:
        await trust.settle(s, org)
        await s.commit()
    assert await _clean(org) == 1
    async with org_scoped_session(org) as s:
        await trust.record_incident(s, org, ACTION, reason="customer_complaint")
        await s.commit()
    assert await _clean(org) == 0  # reset
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT tightened_to_tier, expires_at FROM incident_tightening "
            "WHERE org_id=$1 AND action_type=$2", org, ACTION)
    finally:
        await conn.close()
    assert row["tightened_to_tier"] == 2
    assert row["expires_at"] > datetime.now(UTC) + timedelta(days=13)  # ~14d, self-expiring


async def test_72h_boundary_incident_at_71h59m_blocks_increment(org: uuid.UUID) -> None:
    await _approved(org, decided_hours_ago=73)  # window ended 1h ago
    now = datetime.now(UTC)
    async with org_scoped_session(org) as s:
        # an incident 71h59m after the decision — inside the clean window
        await trust.record_incident(s, org, ACTION, now=now - timedelta(hours=1, minutes=1))
        n = await trust.settle(s, org)
        await s.commit()
    assert n == 0 and await _clean(org) == 0  # the in-window incident blocked the increment


async def test_demotion_offer_is_digest_only_never_applied(org: uuid.UUID) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO trust_ledger (org_id, action_type, clean_approvals) VALUES ($1,$2,$3)",
            org, ACTION, trust.DEMOTION_THRESHOLD)
        before = await conn.fetchval(
            "SELECT count(*) FROM approval_policies WHERE org_id=$1", org)
    finally:
        await conn.close()
    async with org_scoped_session(org) as s:
        offers = await trust.demotion_offers(s, org)
    assert offers and offers[0]["action_type"] == ACTION
    assert offers[0]["offer"] == "loosen_one_tier" and offers[0]["requires"] == "owner_approval"
    conn = await asyncpg.connect(_dsn())
    try:  # IDL-007: computing offers never writes a tenant policy row
        after = await conn.fetchval(
            "SELECT count(*) FROM approval_policies WHERE org_id=$1", org)
    finally:
        await conn.close()
    assert after == before
