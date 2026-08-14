"""Per-store cost & margin (CP-6) — `/v1/admin/billing/tenants/{org_id}/cost-margin`.

Itemised monthly breakdown folding recorded charges (revenue + GO cost per type) with the runtime's
LLM spend (`costs_lite`, USD→INR). LLM is in-plan (revenue 0); platform APIs are separate lines.
Operator-gated. Rigorous corner-case coverage. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import asyncpg
import httpx
import pytest

from core.billing.cost_margin import usd_to_minor
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.auth import issue_access_token

MONTH = "2026-07"
MONTH_START = date(2026, 7, 1)


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.costs_lite')"))
    finally:
        await conn.close()


def _op(user: uuid.UUID) -> dict[str, str]:
    token = issue_access_token(
        sub=str(user), secret=get_settings().jwt_secret, org_id=None, roles=[])
    return {"Authorization": f"Bearer {token}"}


async def _charge(org: uuid.UUID, ctype: str, amount: int, cost: int,
                  when: date = MONTH_START) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO billing_charges (org_id, period_month, charge_type, amount_minor, "
            "cost_minor) VALUES ($1,$2,$3,$4,$5)", org, when, ctype, amount, cost)
    finally:
        await conn.close()


async def _llm(org: uuid.UUID, cost_usd: str, *, tin: int = 100, tout: int = 50,
               when: datetime | None = None) -> None:
    when = when or datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO costs_lite (org_id, node_key, provider, model, tokens_in, tokens_out, "
            "cost_usd, created_at) VALUES ($1,'converse','anthropic','claude-sonnet-5',$2,$3,"
            "$4,$5)", org, tin, tout, Decimal(cost_usd), when)
    finally:
        await conn.close()


@dataclass
class Scene:
    client: httpx.AsyncClient
    operator: uuid.UUID
    org: uuid.UUID
    tag: str


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/costs_lite not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    operator, org = uuid.uuid4(), uuid.uuid4()
    tag = operator.hex[:8]
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           operator, f"op+{tag}@example.test")
        await conn.execute("INSERT INTO platform_admins (user_id, role) VALUES ($1,'admin')",
                           operator)
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,$2)",
                           org, f"CostStore-{tag}-A")
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, operator, org, tag)
    conn = await asyncpg.connect(_dsn())
    try:  # org delete cascades billing_charges + costs_lite
        await conn.execute("DELETE FROM organizations WHERE name LIKE $1", f"CostStore-{tag}%")
        await conn.execute(
            "ALTER TABLE platform_access_log DISABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_access_log WHERE actor_user_id=$1", operator)
        await conn.execute(
            "ALTER TABLE platform_access_log ENABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", operator)
        await conn.execute("DELETE FROM users WHERE id=$1", operator)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _cm_url(org: uuid.UUID, month: str = MONTH) -> str:
    return f"/v1/admin/billing/tenants/{org}/cost-margin?month={month}"


def _lines(body: dict) -> dict[str, dict]:
    return {ln["category"]: ln for ln in body["lines"]}


async def test_itemised_breakdown_and_totals(scene: Scene) -> None:
    await _charge(scene.org, "subscription", 500_000, 0)
    await _charge(scene.org, "whatsapp", 100_000, 60_000)
    await _charge(scene.org, "instagram", 50_000, 30_000)
    await _charge(scene.org, "google_ads", 0, 20_000)  # cost-only (GO paid, didn't bill)
    await _llm(scene.org, "1.50")
    await _llm(scene.org, "0.50")  # total $2.00

    r = await scene.client.get(_cm_url(scene.org), headers=_op(scene.operator))
    assert r.status_code == 200, r.text
    body = r.json()
    rate = get_settings().usd_inr_rate
    llm_minor = usd_to_minor(Decimal("2.00"), rate)

    lines = _lines(body)
    assert lines["subscription"] == {
        "category": "subscription", "label": "Subscription (plan)",
        "revenue_minor": 500_000, "cost_minor": 0, "margin_minor": 500_000}
    assert lines["whatsapp"]["revenue_minor"] == 100_000
    assert lines["whatsapp"]["cost_minor"] == 60_000
    assert lines["whatsapp"]["margin_minor"] == 40_000
    assert lines["google_ads"]["margin_minor"] == -20_000  # cost-only line is negative
    # LLM: in-plan (revenue 0), pure cost, converted from USD
    assert lines["llm"]["revenue_minor"] == 0 and lines["llm"]["cost_minor"] == llm_minor
    assert lines["llm"]["margin_minor"] == -llm_minor
    assert body["llm"]["runs"] == 2 and body["llm"]["cost_usd"] == "2.0000"
    assert body["llm"]["cost_minor"] == llm_minor

    # totals net out
    exp_rev = 650_000
    exp_cost = 60_000 + 30_000 + 20_000 + llm_minor
    assert body["revenue_minor"] == exp_rev
    assert body["cost_minor"] == exp_cost
    assert body["margin_minor"] == exp_rev - exp_cost
    assert body["month"] == MONTH and body["currency"] == "INR"


async def test_empty_month_is_zeroed_but_shows_subscription_and_llm(scene: Scene) -> None:
    r = await scene.client.get(_cm_url(scene.org), headers=_op(scene.operator))
    assert r.status_code == 200, r.text
    body = r.json()
    cats = {ln["category"] for ln in body["lines"]}
    assert cats == {"subscription", "llm"}  # the always-shown lines, both zero
    assert body["revenue_minor"] == 0 and body["cost_minor"] == 0 and body["margin_minor"] == 0


async def test_month_filter_excludes_other_months(scene: Scene) -> None:
    await _charge(scene.org, "whatsapp", 100_000, 60_000, when=date(2026, 6, 1))  # June
    await _llm(scene.org, "5.00", when=datetime(2026, 6, 15, 10, 0, tzinfo=UTC))  # June
    r = await scene.client.get(_cm_url(scene.org), headers=_op(scene.operator))  # July
    body = r.json()
    assert body["revenue_minor"] == 0 and body["llm"]["runs"] == 0  # June data excluded
    assert {ln["category"] for ln in body["lines"]} == {"subscription", "llm"}


async def test_bad_month_is_422(scene: Scene) -> None:
    r = await scene.client.get(
        f"/v1/admin/billing/tenants/{scene.org}/cost-margin?month=2026-13-99",
        headers=_op(scene.operator))
    assert r.status_code == 422


async def test_cost_margin_is_org_scoped(scene: Scene) -> None:
    await _charge(scene.org, "whatsapp", 100_000, 60_000)
    await _llm(scene.org, "2.00")
    other = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,$2)",
                           other, f"CostStore-{scene.tag}-B")
    finally:
        await conn.close()
    # org B sees none of org A's charges/LLM (RLS)
    body = (await scene.client.get(_cm_url(other), headers=_op(scene.operator))).json()
    assert body["revenue_minor"] == 0 and body["cost_minor"] == 0 and body["llm"]["runs"] == 0


async def test_non_operator_is_403(scene: Scene) -> None:
    r = await scene.client.get(_cm_url(scene.org), headers=_op(uuid.uuid4()))
    assert r.status_code == 403


async def test_plane_disabled_is_404(scene: Scene, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "false")
    r = await scene.client.get(_cm_url(scene.org), headers=_op(scene.operator))
    assert r.status_code == 404
