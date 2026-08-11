"""Monthly invoices from charges (OC12) against real Postgres.

Proves the operator can list a store's monthly invoices (one per month with charges, newest first)
and fetch one statement — line items by channel that sum to the total, a deterministic number
(`{STORE}-INV-{YYMM}`), the buyer name — with **GO's cost/margin never exposed**, org-isolated, and
404 for a month with no charges / 400 for a bad month. Skips when the DB is down.
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
        return bool(await conn.fetchval("SELECT to_regclass('public.billing_charges')"))
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


AUG = date(2026, 8, 1)
JUL = date(2026, 7, 1)


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/billing_charges not ready")
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
        await conn.execute(
            "INSERT INTO organizations (id, name) VALUES ($1,'Ratna Store'),($2,'Beta')",
            org_a, org_b)

        async def charge(org, pm, ct, amount, cost):
            await conn.execute(
                "INSERT INTO billing_charges "
                "(org_id, period_month, charge_type, amount_minor, cost_minor) "
                "VALUES ($1,$2,$3,$4,$5)", org, pm, ct, amount, cost)

        # org A · August: subscription 5M (cost 0) + whatsapp 2M (cost 800k) → total 7M
        await charge(org_a, AUG, "subscription", 5_000_000, 0)
        await charge(org_a, AUG, "whatsapp", 2_000_000, 800_000)
        # org A · July: seo 1M
        await charge(org_a, JUL, "seo", 1_000_000, 100_000)
        # org B (isolation)
        await charge(org_b, AUG, "google_ads", 9_999_999, 5_000_000)
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, operator, org_a, org_b)
    conn = await asyncpg.connect(_dsn())
    try:
        for org in (org_a, org_b):
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


async def test_lists_one_invoice_per_month_newest_first(scene: Scene) -> None:
    rows = (await scene.client.get(
        f"/v1/admin/billing/tenants/{scene.org_a}/invoices", headers=_op(scene.operator))).json()
    assert [r["period_month"] for r in rows] == ["2026-08", "2026-07"]  # newest first
    aug = rows[0]
    assert aug["invoice_no"] == "RATN-INV-2608"   # store code · INV · YYMM
    assert aug["total_minor"] == 7_000_000


async def test_statement_sums_line_items_and_hides_cost(scene: Scene) -> None:
    inv = (await scene.client.get(
        f"/v1/admin/billing/tenants/{scene.org_a}/invoices/2026-08",
        headers=_op(scene.operator))).json()
    assert inv["invoice_no"] == "RATN-INV-2608"
    assert inv["buyer_name"] == "Ratna Store"
    channels = {li["charge_type"]: li["amount_minor"] for li in inv["line_items"]}
    assert channels == {"subscription": 5_000_000, "whatsapp": 2_000_000}
    assert sum(channels.values()) == inv["total_minor"] == 7_000_000
    # GO's internal cost / margin must never reach a client invoice.
    resp_text = (await scene.client.get(
        f"/v1/admin/billing/tenants/{scene.org_a}/invoices/2026-08",
        headers=_op(scene.operator))).text.lower()
    assert "cost" not in resp_text and "margin" not in resp_text


async def test_isolated_between_stores(scene: Scene) -> None:
    b = (await scene.client.get(
        f"/v1/admin/billing/tenants/{scene.org_b}/invoices", headers=_op(scene.operator))).json()
    assert [r["period_month"] for r in b] == ["2026-08"]  # only B's own charge
    assert b[0]["invoice_no"] == "BETA-INV-2608"


async def test_month_without_charges_is_404(scene: Scene) -> None:
    r = await scene.client.get(
        f"/v1/admin/billing/tenants/{scene.org_a}/invoices/2026-01", headers=_op(scene.operator))
    assert r.status_code == 404


async def test_bad_month_is_400(scene: Scene) -> None:
    r = await scene.client.get(
        f"/v1/admin/billing/tenants/{scene.org_a}/invoices/nope", headers=_op(scene.operator))
    assert r.status_code == 400


async def test_invoices_403_for_non_operator(scene: Scene) -> None:
    r = await scene.client.get(
        f"/v1/admin/billing/tenants/{scene.org_a}/invoices", headers=_op(uuid.uuid4()))
    assert r.status_code == 403
