"""Owner-facing transparency statement (OC6) against real Postgres.

Proves `GET /v1/insights/transparency`: the owner sees THEIR OWN spend grouped by channel (biggest
first) + the month's revenue + ROAS/ROI — scoped to their org (org B never leaks in), filtered to
the requested month, and — critically — **GO's internal `cost_minor`/margin is never exposed**.
Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date

import asyncpg
import httpx
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
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
        return bool(await conn.fetchval("SELECT to_regclass('public.billing_charges')"))
    finally:
        await conn.close()


def _tok(user: uuid.UUID, org: uuid.UUID | None) -> dict[str, str]:
    token = issue_access_token(
        sub=str(user), secret=get_settings().jwt_secret,
        org_id=str(org) if org else None, roles=[ROLE_OWNER])
    return {"Authorization": f"Bearer {token}"}


def _first_of_month(d: date) -> date:
    return d.replace(day=1)


def _prev_month(d: date) -> date:
    first = d.replace(day=1)
    return (first.replace(year=first.year - 1, month=12) if first.month == 1
            else first.replace(month=first.month - 1))


async def _charge(
    conn: asyncpg.Connection, org: uuid.UUID, period: date, ctype: str, amount: int, cost: int,
) -> None:
    await conn.execute(
        "INSERT INTO billing_charges (org_id, period_month, charge_type, amount_minor, cost_minor) "
        "VALUES ($1,$2,$3,$4,$5)", org, period, ctype, amount, cost)


async def _order(conn: asyncpg.Connection, org: uuid.UUID, total: int) -> None:
    contact = await conn.fetchval("INSERT INTO contacts (org_id) VALUES ($1) RETURNING id", org)
    await conn.execute(
        "INSERT INTO orders (org_id, contact_id, items, total_minor) "
        "VALUES ($1,$2,'[]'::jsonb,$3)", org, contact, total)


@dataclass
class Scene:
    client: httpx.AsyncClient
    org_a: uuid.UUID
    org_b: uuid.UUID
    user_a: uuid.UUID


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/billing_charges not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org_a, org_b, user_a = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    this_month = _first_of_month(date.today())
    last_month = _prev_month(date.today())
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'A'),($2,'B')",
                           org_a, org_b)
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           user_a, f"a+{user_a.hex[:8]}@example.test")
        # org A, this month: subscription 5M, whatsapp 2M+1M (two rows → 3M), instagram 1.5M.
        await _charge(conn, org_a, this_month, "subscription", 5_000_000, 0)
        await _charge(conn, org_a, this_month, "whatsapp", 2_000_000, 800_000)
        await _charge(conn, org_a, this_month, "whatsapp", 1_000_000, 300_000)
        await _charge(conn, org_a, this_month, "instagram", 1_500_000, 900_000)
        # org A, last month (must be excluded from the default view).
        await _charge(conn, org_a, last_month, "seo", 7_000_000, 100_000)
        # org A revenue this month: 4M + 6M = 10M.
        await _order(conn, org_a, 4_000_000)
        await _order(conn, org_a, 6_000_000)
        # org B (isolation): different spend that must never appear for A.
        await _charge(conn, org_b, this_month, "google_ads", 9_999_999, 5_000_000)
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, org_a, org_b, user_a)
    conn = await asyncpg.connect(_dsn())
    try:
        for org in (org_a, org_b):
            await conn.execute("DELETE FROM billing_charges WHERE org_id=$1", org)
            await conn.execute("DELETE FROM orders WHERE org_id=$1", org)
            await conn.execute("DELETE FROM contacts WHERE org_id=$1", org)
        await conn.execute("DELETE FROM users WHERE id=$1", user_a)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", [org_a, org_b])
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_groups_spend_by_channel_hides_cost_and_computes_roi(scene: Scene) -> None:
    r = await scene.client.get("/v1/insights/transparency", headers=_tok(scene.user_a, scene.org_a))
    assert r.status_code == 200, r.text
    body = r.json()

    channels = body["spend_by_channel"]
    assert [c["channel"] for c in channels] == ["subscription", "whatsapp", "instagram"]  # desc
    assert [c["amount_minor"] for c in channels] == [5_000_000, 3_000_000, 1_500_000]  # rows summed
    assert body["total_spend_minor"] == 9_500_000  # last month's seo excluded
    assert body["revenue_minor"] == 10_000_000
    assert body["roas"] == round(10_000_000 / 9_500_000, 2)
    assert body["roi_pct"] is not None

    # GO's internal cost / margin must NEVER reach the store owner.
    assert "cost" not in r.text.lower()
    assert "margin" not in r.text.lower()


async def test_month_filter(scene: Scene) -> None:
    last = _prev_month(date.today()).strftime("%Y-%m")
    r = await scene.client.get(
        f"/v1/insights/transparency?month={last}", headers=_tok(scene.user_a, scene.org_a))
    body = r.json()
    assert [c["channel"] for c in body["spend_by_channel"]] == ["seo"]
    assert body["total_spend_minor"] == 7_000_000


async def test_isolation_org_b_spend_never_appears(scene: Scene) -> None:
    r = await scene.client.get("/v1/insights/transparency", headers=_tok(scene.user_a, scene.org_a))
    channels = {c["channel"] for c in r.json()["spend_by_channel"]}
    assert "google_ads" not in channels  # org B's channel
    assert "9999999" not in r.text


async def test_bad_month_is_400(scene: Scene) -> None:
    r = await scene.client.get(
        "/v1/insights/transparency?month=nonsense", headers=_tok(scene.user_a, scene.org_a))
    assert r.status_code == 400


async def test_requires_auth_and_org(scene: Scene) -> None:
    assert (await scene.client.get("/v1/insights/transparency")).status_code == 401
    r = await scene.client.get("/v1/insights/transparency", headers=_tok(scene.user_a, None))
    assert r.status_code == 400  # authenticated but no org context
