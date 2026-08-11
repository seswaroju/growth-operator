"""Per-channel budgets & caps (OC7) against real Postgres.

Proves an operator can set a per-channel monthly budget, read it back with month-to-date spend +
over flag, that an ENFORCED cap blocks an over-budget charge (429 `budget_exceeded`) while an
alert-only budget lets it through (just flagged `over`), a channel with no budget is unaffected,
budgets are org-isolated, and delete works. Skips when the DB is down.
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


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.channel_budgets')"))
    finally:
        await conn.close()


def _op(user: uuid.UUID) -> dict[str, str]:
    token = issue_access_token(
        sub=str(user), secret=get_settings().jwt_secret, org_id=None, roles=[])
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Scene:
    client: httpx.AsyncClient
    operator: uuid.UUID
    org_a: uuid.UUID
    org_b: uuid.UUID


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/channel_budgets not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    operator, org_a, org_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           operator, f"op+{operator.hex[:8]}@example.test")
        await conn.execute("INSERT INTO platform_admins (user_id, role) VALUES ($1,'admin')",
                           operator)
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'A'),($2,'B')",
                           org_a, org_b)
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, operator, org_a, org_b)
    conn = await asyncpg.connect(_dsn())
    try:
        for org in (org_a, org_b):
            await conn.execute("DELETE FROM channel_budgets WHERE org_id=$1", org)
            await conn.execute("DELETE FROM billing_charges WHERE org_id=$1", org)
        await conn.execute(
            "ALTER TABLE platform_access_log DISABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_access_log WHERE actor_user_id=$1", operator)
        await conn.execute(
            "ALTER TABLE platform_access_log ENABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", operator)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", [org_a, org_b])
        await conn.execute("DELETE FROM users WHERE id=$1", operator)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _month() -> str:
    return date.today().replace(day=1).isoformat()


async def _set_budget(scene: Scene, org: uuid.UUID, channel: str, budget: int, enforce: bool):
    return await scene.client.put(
        f"/v1/admin/billing/tenants/{org}/budgets/{channel}", headers=_op(scene.operator),
        json={"budget_minor": budget, "enforce": enforce})


async def _charge(scene: Scene, org: uuid.UUID, channel: str, amount: int) -> httpx.Response:
    return await scene.client.post(
        f"/v1/admin/billing/tenants/{org}/charges", headers=_op(scene.operator),
        json={"period_month": _month(), "charge_type": channel, "amount_minor": amount,
              "cost_minor": 0})


async def test_set_list_status_with_mtd_spend(scene: Scene) -> None:
    assert (await _set_budget(scene, scene.org_a, "whatsapp", 1_000_000, False)).status_code == 200
    await _charge(scene, scene.org_a, "whatsapp", 300_000)
    await _charge(scene, scene.org_a, "whatsapp", 200_000)

    rows = (await scene.client.get(
        f"/v1/admin/billing/tenants/{scene.org_a}/budgets", headers=_op(scene.operator))).json()
    wa = next(r for r in rows if r["charge_type"] == "whatsapp")
    assert wa["budget_minor"] == 1_000_000
    assert wa["spent_minor"] == 500_000        # month-to-date
    assert wa["remaining_minor"] == 500_000
    assert wa["pct"] == 50.0 and wa["over"] is False


async def test_enforced_cap_blocks_over_budget_charge(scene: Scene) -> None:
    await _set_budget(scene, scene.org_a, "instagram", 1_000_000, True)  # enforce
    assert (await _charge(scene, scene.org_a, "instagram", 800_000)).status_code == 201
    blocked = await _charge(scene, scene.org_a, "instagram", 300_000)   # 1.1M > 1.0M
    assert blocked.status_code == 429                                    # budget_exceeded
    assert "budget_exceeded" in blocked.text
    # the blocked charge did not persist
    listed = (await scene.client.get(
        f"/v1/admin/billing/tenants/{scene.org_a}/charges", headers=_op(scene.operator))).json()
    assert sum(c["amount_minor"] for c in listed if c["charge_type"] == "instagram") == 800_000


async def test_alert_only_budget_allows_but_flags_over(scene: Scene) -> None:
    await _set_budget(scene, scene.org_a, "google_ads", 500_000, False)  # alert-only
    assert (await _charge(scene, scene.org_a, "google_ads", 900_000)).status_code == 201  # allowed
    rows = (await scene.client.get(
        f"/v1/admin/billing/tenants/{scene.org_a}/budgets", headers=_op(scene.operator))).json()
    ga = next(r for r in rows if r["charge_type"] == "google_ads")
    assert ga["over"] is True and ga["remaining_minor"] == -400_000


async def test_channel_without_budget_is_unaffected(scene: Scene) -> None:
    # No budget for 'seo' → a large charge always goes through.
    assert (await _charge(scene, scene.org_a, "seo", 99_000_000)).status_code == 201


async def test_budgets_are_org_isolated(scene: Scene) -> None:
    await _set_budget(scene, scene.org_a, "whatsapp", 1_000_000, False)
    b = (await scene.client.get(
        f"/v1/admin/billing/tenants/{scene.org_b}/budgets", headers=_op(scene.operator))).json()
    assert b == []  # org B has no budgets even though org A does


async def test_delete_budget(scene: Scene) -> None:
    await _set_budget(scene, scene.org_a, "whatsapp", 1_000_000, True)
    d = await scene.client.delete(
        f"/v1/admin/billing/tenants/{scene.org_a}/budgets/whatsapp", headers=_op(scene.operator))
    assert d.status_code == 204
    # gone → an over-cap charge now succeeds
    assert (await _charge(scene, scene.org_a, "whatsapp", 5_000_000)).status_code == 201
    d404 = await scene.client.delete(
        f"/v1/admin/billing/tenants/{scene.org_a}/budgets/whatsapp", headers=_op(scene.operator))
    assert d404.status_code == 404
