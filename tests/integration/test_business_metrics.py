"""Business-metrics rollup + weekly summary against real Postgres (Phase 3.5-eng, Ticket A1).

Proves the daily rollup counts the domain tables correctly, upserts idempotently, is org-scoped, and
that `GET /v1/insights/summary` returns this-week vs last-week outcomes with deltas. Skips when the
DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.insights import metrics, rollup
from core.tenancy.auth import issue_access_token
from core.tenancy.permissions import ROLE_OWNER


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.business_metrics')"))
    finally:
        await conn.close()


def _tok(user: uuid.UUID, org: uuid.UUID | None, roles: tuple[str, ...] = (ROLE_OWNER,)) -> str:
    return issue_access_token(sub=str(user), secret=get_settings().jwt_secret,
                             org_id=str(org) if org else None, roles=list(roles))


async def _seed_day(
    conn: asyncpg.Connection, org: uuid.UUID, ct: uuid.UUID, conv: uuid.UUID, days_ago: int, *,
    leads: int, order_total: int | None, msgs_in: int, msgs_out: int,
) -> None:
    ts = datetime.now(UTC) - timedelta(days=days_ago)
    for _ in range(leads):
        await conn.execute(
            "INSERT INTO leads (org_id, contact_id, source, stage, intent, created_at) "
            "VALUES ($1,$2,'whatsapp','new','{}'::jsonb,$3)", org, ct, ts)
    if order_total is not None:
        await conn.execute(
            "INSERT INTO orders (org_id, contact_id, items, total_minor, created_at) "
            "VALUES ($1,$2,'[]'::jsonb,$3,$4)", org, ct, order_total, ts)
    msg = ("INSERT INTO messages (org_id, conversation_id, direction, sender, body, status, "
           "created_at) VALUES ($1,$2,$3,$4,'m','received',$5)")
    for _ in range(msgs_in):
        await conn.execute(msg, org, conv, "inbound", "customer", ts)
    for _ in range(msgs_out):
        await conn.execute(msg, org, conv, "outbound", "store", ts)


@dataclass
class Scene:
    client: httpx.AsyncClient
    org_a: uuid.UUID
    org_b: uuid.UUID
    user_a: uuid.UUID

    def hdr(self, user: uuid.UUID, org: uuid.UUID | None,
            roles: tuple[str, ...] = (ROLE_OWNER,)) -> dict[str, str]:
        return {"Authorization": f"Bearer {_tok(user, org, roles)}"}


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/business_metrics not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    user_a = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id,name) VALUES ($1,'Alpha'),($2,'Beta')",
                           org_a, org_b)
        await conn.execute("INSERT INTO users (id,email) VALUES ($1,$2)",
                           user_a, f"{user_a}@example.test")
        for org in (org_a, org_b):
            ch = await conn.fetchval(
                "INSERT INTO channels (org_id,type,external_id,credentials_ref) "
                "VALUES ($1,'whatsapp',$2,'ref') RETURNING id", org, f"e-{uuid.uuid4()}")
            ct = await conn.fetchval(
                "INSERT INTO contacts (org_id) VALUES ($1) RETURNING id", org)
            conv = await conn.fetchval(
                "INSERT INTO conversations (org_id,contact_id,channel_id,status) "
                "VALUES ($1,$2,$3,'open') RETURNING id", org, ct, ch)
            # Alpha gets both weeks; Beta only this week (isolation check)
            await _seed_day(conn, org, ct, conv, 2, leads=2, order_total=500000,
                            msgs_in=3, msgs_out=2)
            if org == org_a:
                await _seed_day(conn, org, ct, conv, 10, leads=1, order_total=300000,
                                msgs_in=1, msgs_out=1)
    finally:
        await conn.close()
    from core.api.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Scene(client, org_a, org_b, user_a)
    conn = await asyncpg.connect(_dsn())
    try:
        orgs = [org_a, org_b]
        for tbl in ("business_metrics", "messages", "orders", "leads", "conversations",
                    "contacts", "channels"):
            await conn.execute(f"DELETE FROM {tbl} WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM users WHERE id=$1", user_a)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _rollup(org: uuid.UUID) -> None:
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        await rollup.rollup_org(s, org)
        await s.commit()


async def test_compute_day_counts_the_domain_tables(scene: Scene) -> None:
    # UTC basis to match the seeding (`datetime.now(UTC)`); a local `date.today()` is off by one
    # when the local/UTC date boundary differs.
    day = datetime.now(UTC).date() - timedelta(days=2)
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        vals = await metrics.compute_day(s, scene.org_a, day)
    assert vals["leads_created"] == 2
    assert vals["orders"] == 1 and vals["revenue_minor"] == 500000
    assert vals["messages_in"] == 3 and vals["messages_out"] == 2
    assert vals["quotes_sent"] == 0  # none seeded


async def test_rollup_is_idempotent(scene: Scene) -> None:
    await _rollup(scene.org_a)
    await _rollup(scene.org_a)  # second run must not duplicate rows
    conn = await asyncpg.connect(_dsn())
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM business_metrics WHERE org_id=$1 AND metric_key='leads_created' "
            "AND metric_date=$2", scene.org_a, datetime.now(UTC).date() - timedelta(days=2))
    finally:
        await conn.close()
    assert n == 1


async def test_weekly_summary_wow_delta(scene: Scene) -> None:
    await _rollup(scene.org_a)
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        summary = {m.metric_key: m for m in await metrics.weekly_summary(s, scene.org_a)}
    assert (summary["leads_created"].this_week, summary["leads_created"].last_week) == (2, 1)
    assert summary["leads_created"].delta_pct == 100.0
    rev = summary["revenue_minor"]
    assert (rev.this_week, rev.last_week) == (500000, 300000)


async def test_metrics_are_org_scoped(scene: Scene) -> None:
    # Beta seeded only this-week 2 leads; its summary must not include Alpha's rows.
    await _rollup(scene.org_b)
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        summary = {m.metric_key: m for m in await metrics.weekly_summary(s, scene.org_b)}
    assert summary["leads_created"].this_week == 2 and summary["leads_created"].last_week == 0


async def test_summary_endpoint(scene: Scene) -> None:
    await _rollup(scene.org_a)
    r = await scene.client.get("/v1/insights/summary", headers=scene.hdr(scene.user_a, scene.org_a))
    assert r.status_code == 200, r.text
    lc = {m["metric_key"]: m for m in r.json()}["leads_created"]
    assert lc["this_week"] == 2 and lc["delta_pct"] == 100.0


async def test_summary_requires_permission(scene: Scene) -> None:
    r = await scene.client.get("/v1/insights/summary",
                               headers=scene.hdr(scene.user_a, scene.org_a, roles=()))
    assert r.status_code == 403
