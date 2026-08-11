"""Billing operator API (B1) against real Postgres — the per-client revenue model behind P4.6.

Proves an operator can define a plan, put a client on it, and record a managed-service charge
(amount the client pays + cost we pay), that the cross-client rollup reflects MRR + this-month
revenue/cost/**margin = amount − cost**, that a client's charges are org-isolated, and that the
surface is operator-only (403/401/404). Skips when the DB is unreachable.
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
    plan_name: str


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/billing not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    operator, org_a, org_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    plan_name = f"Growth-{operator.hex[:6]}"
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           operator, f"op+{operator.hex[:8]}@example.test")
        await conn.execute("INSERT INTO platform_admins (user_id, role) VALUES ($1,'admin')",
                           operator)
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Alpha'),($2,'Beta')",
                           org_a, org_b)
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, operator, org_a, org_b, plan_name)
    conn = await asyncpg.connect(_dsn())
    try:
        orgs = [org_a, org_b]
        await conn.execute("DELETE FROM billing_charges WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM billing_subscriptions WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM billing_plans WHERE name LIKE $1", plan_name + "%")
        await conn.execute(
            "ALTER TABLE platform_access_log DISABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_access_log WHERE actor_user_id=$1", operator)
        await conn.execute(
            "ALTER TABLE platform_access_log ENABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", operator)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM users WHERE id=$1", operator)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_plan_subscription_charge_and_rollup_margin(scene: Scene) -> None:
    op = _op(scene.operator)
    before = (await scene.client.get("/v1/admin/billing/rollup", headers=op)).json()

    # define a plan, put Alpha on it
    plan = await scene.client.post(
        "/v1/admin/billing/plans", headers=op,
        json={"name": scene.plan_name, "price_minor": 500_000})
    assert plan.status_code == 201, plan.text
    plan_id = plan.json()["id"]
    assign = await scene.client.post(
        f"/v1/admin/billing/tenants/{scene.org_a}/subscription", headers=op,
        json={"plan_id": plan_id})
    assert assign.status_code == 204, assign.text

    sub = await scene.client.get(
        f"/v1/admin/billing/tenants/{scene.org_a}/subscription", headers=op)
    assert sub.json()["plan_name"] == scene.plan_name and sub.json()["status"] == "active"

    # record a managed-service charge: client pays 2,000, we spend 1,500 → margin 500
    charge = await scene.client.post(
        f"/v1/admin/billing/tenants/{scene.org_a}/charges", headers=op,
        json={"period_month": date.today().isoformat(), "charge_type": "social",
              "amount_minor": 200_000, "cost_minor": 150_000, "note": "Meta ads"})
    assert charge.status_code == 201, charge.text

    after = (await scene.client.get("/v1/admin/billing/rollup", headers=op)).json()

    def d(k: str) -> int:
        return after[k] - before[k]

    assert d("mrr_minor") == 500_000           # the active plan price
    assert d("charges_revenue_minor") == 200_000
    assert d("charges_cost_minor") == 150_000
    assert d("margin_minor") == 500_000 + 200_000 - 150_000   # MRR + revenue − cost
    assert d("active_clients") == 1


async def test_a_clients_charges_are_org_isolated(scene: Scene) -> None:
    op = _op(scene.operator)
    await scene.client.post(
        f"/v1/admin/billing/tenants/{scene.org_a}/charges", headers=op,
        json={"period_month": date.today().isoformat(), "charge_type": "seo",
              "amount_minor": 100_000})
    a = await scene.client.get(f"/v1/admin/billing/tenants/{scene.org_a}/charges", headers=op)
    b = await scene.client.get(f"/v1/admin/billing/tenants/{scene.org_b}/charges", headers=op)
    assert len(a.json()) == 1 and len(b.json()) == 0  # Alpha's charge never shows under Beta


async def test_billing_403_for_non_operator(scene: Scene) -> None:
    r = await scene.client.get("/v1/admin/billing/rollup", headers=_op(uuid.uuid4()))
    assert r.status_code == 403


async def test_billing_401_without_token(scene: Scene) -> None:
    r = await scene.client.get("/v1/admin/billing/rollup")
    assert r.status_code == 401


async def test_billing_404_when_plane_disabled(
    scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "false")
    r = await scene.client.get("/v1/admin/billing/rollup", headers=_op(scene.operator))
    assert r.status_code == 404


# ---- OC1: editable plans + "what's included" ---------------------------------------------------

async def test_create_and_edit_plan_features(scene: Scene) -> None:
    op = _op(scene.operator)
    # create with description + features
    created = await scene.client.post(
        "/v1/admin/billing/plans", headers=op,
        json={"name": scene.plan_name, "price_minor": 500_000, "description": "Mid tier",
              "features": ["WhatsApp campaigns", "SEO — 8 keywords"]})
    assert created.status_code == 201, created.text
    body = created.json()
    plan_id = body["id"]
    assert body["description"] == "Mid tier"
    assert body["features"] == ["WhatsApp campaigns", "SEO — 8 keywords"]

    # edit: change price, description, features, and deactivate
    edited = await scene.client.patch(
        f"/v1/admin/billing/plans/{plan_id}", headers=op,
        json={"name": scene.plan_name, "price_minor": 750_000, "active": False,
              "description": "Upgraded", "features": ["Google Ads", "Monthly report"]})
    assert edited.status_code == 200, edited.text
    e = edited.json()
    assert e["price_minor"] == 750_000 and e["active"] is False
    assert e["description"] == "Upgraded"
    assert e["features"] == ["Google Ads", "Monthly report"]

    # the change is durable (round-trips through list)
    listed = (await scene.client.get("/v1/admin/billing/plans", headers=op)).json()
    row = next(p for p in listed if p["id"] == plan_id)
    assert row["features"] == ["Google Ads", "Monthly report"] and row["active"] is False


async def test_create_and_edit_plan_seats_and_config(scene: Scene) -> None:
    # CP-1: a plan is a full template — seat limits + functional config (which agents/channels are
    # on, add-ons) — and it is editable.
    op = _op(scene.operator)
    created = await scene.client.post(
        "/v1/admin/billing/plans", headers=op,
        json={"name": scene.plan_name, "price_minor": 500_000, "max_managers": 1, "max_staff": 2,
              "config": {"agents": ["concierge", "nurture"], "channels": ["whatsapp"],
                         "addons": ["instagram"]}})
    assert created.status_code == 201, created.text
    body = created.json()
    plan_id = body["id"]
    assert body["max_managers"] == 1 and body["max_staff"] == 2
    assert body["config"]["agents"] == ["concierge", "nurture"]
    assert body["config"]["channels"] == ["whatsapp"]

    # edit: bump seats + expand the config (fully editable template)
    edited = await scene.client.patch(
        f"/v1/admin/billing/plans/{plan_id}", headers=op,
        json={"name": scene.plan_name, "price_minor": 750_000, "active": True,
              "max_managers": 3, "max_staff": 10,
              "config": {"agents": ["concierge", "nurture", "campaigner"],
                         "channels": ["whatsapp", "instagram"], "addons": ["instagram", "seo"]}})
    assert edited.status_code == 200, edited.text
    e = edited.json()
    assert e["max_managers"] == 3 and e["max_staff"] == 10
    assert "campaigner" in e["config"]["agents"] and "seo" in e["config"]["addons"]

    # durable through list
    listed = (await scene.client.get("/v1/admin/billing/plans", headers=op)).json()
    row = next(p for p in listed if p["id"] == plan_id)
    assert row["max_staff"] == 10 and row["config"]["channels"] == ["whatsapp", "instagram"]


async def test_edit_plan_404_for_unknown_id(scene: Scene) -> None:
    r = await scene.client.patch(
        f"/v1/admin/billing/plans/{uuid.uuid4()}", headers=_op(scene.operator),
        json={"name": "x", "price_minor": 1000, "active": True, "features": []})
    assert r.status_code == 404


async def test_edit_plan_409_on_duplicate_name(scene: Scene) -> None:
    op = _op(scene.operator)
    p1 = await scene.client.post("/v1/admin/billing/plans", headers=op,
                                 json={"name": scene.plan_name, "price_minor": 100_000})
    p2 = await scene.client.post("/v1/admin/billing/plans", headers=op,
                                 json={"name": scene.plan_name + "-2", "price_minor": 200_000})
    assert p1.status_code == 201 and p2.status_code == 201
    # rename p2 onto p1's (unique) name → conflict
    clash = await scene.client.patch(
        f"/v1/admin/billing/plans/{p2.json()['id']}", headers=op,
        json={"name": scene.plan_name, "price_minor": 200_000, "active": True, "features": []})
    assert clash.status_code == 409


async def test_edit_plan_403_for_non_operator(scene: Scene) -> None:
    r = await scene.client.patch(
        f"/v1/admin/billing/plans/{uuid.uuid4()}", headers=_op(uuid.uuid4()),
        json={"name": "x", "price_minor": 1000, "active": True, "features": []})
    assert r.status_code == 403


# ---- OC2: per-channel charge categories --------------------------------------------------------

async def test_charge_accepts_new_channel_types(scene: Scene) -> None:
    op = _op(scene.operator)
    for ct in ("whatsapp", "instagram", "google_ads"):
        r = await scene.client.post(
            f"/v1/admin/billing/tenants/{scene.org_a}/charges", headers=op,
            json={"period_month": date.today().isoformat(), "charge_type": ct,
                  "amount_minor": 10_000, "cost_minor": 4_000})
        assert r.status_code == 201, r.text
    listed = (await scene.client.get(
        f"/v1/admin/billing/tenants/{scene.org_a}/charges", headers=op)).json()
    assert {"whatsapp", "instagram", "google_ads"} <= {c["charge_type"] for c in listed}
