"""Runtime entitlement enforcement (PLAN-5) against real Postgres.

Two properties are proven together, because either alone would be wrong: paid execution stops when
the plan does not include it, **and** the merchant keeps reading the records they already own after
cancelling. Entitlement governs what the product will *do*, not what the merchant may *see*.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest

from core.api.main import app
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy import auth

FULL = ["conversations", "catalog", "customers", "ghost_recovery",
        "campaigns.whatsapp", "campaigns.analytics", "landing_pages", "catalog.ingestion"]


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.billing_plans')"))
    finally:
        await conn.close()


def _owner(user: uuid.UUID, org: uuid.UUID) -> dict[str, str]:
    token = auth.issue_access_token(
        sub=str(user), secret=get_settings().jwt_secret, org_id=str(org), roles=["owner"])
    return {"Authorization": f"Bearer {token}"}


class Store:
    """One store whose plan can be rewritten between requests."""

    def __init__(self, conn: asyncpg.Connection, tag: str, client: httpx.AsyncClient) -> None:
        self.conn, self.tag, self.client = conn, tag, client
        self.org = uuid.uuid4()
        self.user = uuid.uuid4()
        self.plan = uuid.uuid4()

    @property
    def headers(self) -> dict[str, str]:
        return _owner(self.user, self.org)

    async def setup(self, capabilities: list[str]) -> None:
        await self.conn.execute(
            "INSERT INTO organizations (id, name, vertical) VALUES ($1,$2,'jewelry')",
            self.org, self.tag)
        await self.conn.execute(
            "INSERT INTO users (id, email) VALUES ($1,$2)", self.user, f"{self.tag}@x.test")
        await self.conn.execute(
            "INSERT INTO billing_plans (id, name, price_minor, features, config, max_managers, "
            "max_staff) VALUES ($1,$2,1,'[]'::jsonb,$3::jsonb,1,4)",
            self.plan, f"{self.tag}-plan", json.dumps(self._config(capabilities)))
        await self.conn.execute(
            "INSERT INTO billing_subscriptions (org_id, plan_id, status) VALUES ($1,$2,'active')",
            self.org, self.plan)
        await self.conn.execute(
            "INSERT INTO user_orgs (user_id, org_id, role) VALUES ($1,$2,'owner')",
            self.user, self.org)

    @staticmethod
    def _config(capabilities: list[str]) -> dict:
        return {"entitlement_schema_version": 1, "entitlements": capabilities,
                "agents": ["concierge"], "channels": ["whatsapp"],
                "addons": [], "promotions": [], "vertical": None}

    async def set_capabilities(self, capabilities: list[str]) -> None:
        await self.conn.execute(
            "UPDATE billing_plans SET config = $2::jsonb WHERE id = $1",
            self.plan, json.dumps(self._config(capabilities)))

    async def cancel(self) -> None:
        await self.conn.execute(
            "UPDATE billing_subscriptions SET status='cancelled', cancelled_at=now() "
            "WHERE org_id = $1", self.org)


@pytest.fixture()
async def store() -> AsyncIterator[Store]:
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    tag = f"p5-{uuid.uuid4().hex[:8]}"
    conn = await asyncpg.connect(_dsn())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        s = Store(conn, tag, client)
        await s.setup(FULL)
        try:
            yield s
        finally:
            await conn.execute("DELETE FROM billing_subscriptions WHERE org_id=$1", s.org)
            await conn.execute("DELETE FROM user_orgs WHERE org_id=$1", s.org)
            await conn.execute("DELETE FROM organizations WHERE id=$1", s.org)
            await conn.execute("DELETE FROM users WHERE id=$1", s.user)
            await conn.execute("DELETE FROM billing_plans WHERE id=$1", s.plan)
            await conn.close()
            await dbmod.get_engine().dispose()
            dbmod.get_engine.cache_clear()
            dbmod.get_sessionmaker.cache_clear()


def _denied(r: httpx.Response) -> bool:
    return r.status_code == 403 and "not included in this plan" in r.text


# ---- Paid execution is gated -----------------------------------------------------------------


async def test_catalog_search_gated(store: Store) -> None:
    await store.set_capabilities([c for c in FULL if c != "catalog"])
    assert _denied(await store.client.get("/v1/catalog/search?q=x", headers=store.headers))


async def test_catalog_write_gated(store: Store) -> None:
    await store.set_capabilities([c for c in FULL if c != "catalog"])
    r = await store.client.post("/v1/catalog/items", headers=store.headers, json={"attrs": {}})
    assert _denied(r)


async def test_recovery_override_gated(store: Store) -> None:
    await store.set_capabilities([c for c in FULL if c != "ghost_recovery"])
    r = await store.client.post(
        f"/v1/leads/{uuid.uuid4()}/recovery", headers=store.headers, json={"action": "exclude"})
    assert _denied(r)


async def test_campaign_create_gated(store: Store) -> None:
    await store.set_capabilities([c for c in FULL if c != "campaigns.whatsapp"])
    r = await store.client.post("/v1/campaigns", headers=store.headers, json={"name": "x"})
    assert _denied(r)


async def test_campaign_send_gated(store: Store) -> None:
    await store.set_capabilities([c for c in FULL if c != "campaigns.whatsapp"])
    r = await store.client.post(
        f"/v1/campaigns/{uuid.uuid4()}/send", headers=store.headers, json={"typed_count": 1})
    assert _denied(r)


async def test_audience_preview_gated(store: Store) -> None:
    await store.set_capabilities([c for c in FULL if c != "campaigns.whatsapp"])
    assert _denied(
        await store.client.get("/v1/campaigns/audience-preview", headers=store.headers))


async def test_campaign_analytics_gated(store: Store) -> None:
    await store.set_capabilities([c for c in FULL if c != "campaigns.analytics"])
    assert _denied(await store.client.get(
        f"/v1/campaigns/{uuid.uuid4()}/analytics", headers=store.headers))


async def test_campaign_report_gated(store: Store) -> None:
    await store.set_capabilities([c for c in FULL if c != "campaigns.analytics"])
    assert _denied(await store.client.post(
        f"/v1/campaigns/{uuid.uuid4()}/report", headers=store.headers, json={}))


async def test_landing_create_gated(store: Store) -> None:
    await store.set_capabilities([c for c in FULL if c != "landing_pages"])
    r = await store.client.post("/v1/landing/pages", headers=store.headers, json={"goal": "x"})
    assert _denied(r)


@pytest.mark.parametrize("action", ["select", "submit", "publish", "pause", "rollback", "archive"])
async def test_landing_lifecycle_gated(store: Store, action: str) -> None:
    await store.set_capabilities([c for c in FULL if c != "landing_pages"])
    r = await store.client.post(
        f"/v1/landing/pages/{uuid.uuid4()}/{action}", headers=store.headers, json={})
    assert _denied(r)


async def test_landing_insights_gated(store: Store) -> None:
    await store.set_capabilities([c for c in FULL if c != "landing_pages"])
    assert _denied(await store.client.get(
        f"/v1/landing/pages/{uuid.uuid4()}/insights", headers=store.headers))


async def test_import_create_gated(store: Store) -> None:
    await store.set_capabilities([c for c in FULL if c != "catalog.ingestion"])
    r = await store.client.post(
        "/v1/imports", headers=store.headers, files={"file": ("a.csv", b"x")})
    assert _denied(r)


@pytest.mark.parametrize("stage", ["extract", "validate"])
async def test_import_stage_gated(store: Store, stage: str) -> None:
    await store.set_capabilities([c for c in FULL if c != "catalog.ingestion"])
    r = await store.client.post(
        f"/v1/imports/{uuid.uuid4()}/{stage}", headers=store.headers, json={})
    assert _denied(r)


async def test_import_load_service_gated(store: Store) -> None:
    """The service, not just the route — the load step is where the irreversible work happens."""
    from core.ingestion import load
    from core.tenancy.entitlements import FeatureNotInPlan

    await store.set_capabilities([c for c in FULL if c != "catalog.ingestion"])
    async with dbmod.get_sessionmaker()() as s:
        with pytest.raises(FeatureNotInPlan):
            await load.load_batch(s, store.org, uuid.uuid4())


async def test_import_revert_service_gated(store: Store) -> None:
    from core.ingestion import load
    from core.tenancy.entitlements import FeatureNotInPlan

    await store.set_capabilities([c for c in FULL if c != "catalog.ingestion"])
    async with dbmod.get_sessionmaker()() as s:
        with pytest.raises(FeatureNotInPlan):
            await load.revert_batch(s, store.org, uuid.uuid4())


async def test_manual_rate_gated(store: Store) -> None:
    """Vertical capability, resolved by suffix so `core/` never names the vertical."""
    from core.pricing import rates
    from core.tenancy.entitlements import FeatureNotInPlan

    async with dbmod.get_sessionmaker()() as s:
        with pytest.raises(FeatureNotInPlan):
            await rates.record_manual_rate(
                s, "ibja_gold", {"per_gram_24k": 1}, org_id=store.org, actor_id=store.user)


# ---- Historical data continuity after cancellation --------------------------------------------


@pytest.mark.parametrize("path", [
    "/v1/conversations", "/v1/customers", "/v1/catalog/items", "/v1/leads",
    "/v1/campaigns", "/v1/landing/pages", "/v1/imports", "/v1/approvals", "/v1/rates/status",
])
async def test_cancelled_store_still_reads_its_own_records(store: Store, path: str) -> None:
    """Cancelling stops paid execution; it does not take away the merchant's own history."""
    await store.cancel()
    r = await store.client.get(path, headers=store.headers)
    assert r.status_code == 200, (path, r.status_code, r.text[:200])


async def test_cancelled_store_cannot_execute_anything_paid(store: Store) -> None:
    await store.cancel()
    for method, path, body in [
        ("post", "/v1/campaigns", {"name": "x"}),
        ("post", "/v1/landing/pages", {"goal": "x"}),
        ("get", "/v1/catalog/search?q=x", None),
        ("post", f"/v1/leads/{uuid.uuid4()}/recovery", {"action": "exclude"}),
        ("post", f"/v1/imports/{uuid.uuid4()}/validate", {}),
    ]:
        call = getattr(store.client, method)
        r = await (call(path, headers=store.headers, json=body) if body is not None
                   else call(path, headers=store.headers))
        assert _denied(r), (path, r.status_code, r.text[:160])


async def test_no_subscription_denies_paid_execution(store: Store) -> None:
    await store.conn.execute("DELETE FROM billing_subscriptions WHERE org_id=$1", store.org)
    assert _denied(await store.client.post(
        "/v1/campaigns", headers=store.headers, json={"name": "x"}))
    assert (await store.client.get("/v1/customers", headers=store.headers)).status_code == 200


# ---- RBAC and entitlement are independent ------------------------------------------------------


async def test_rbac_denial_wins_even_with_a_full_plan(store: Store) -> None:
    viewer = auth.issue_access_token(
        sub=str(store.user), secret=get_settings().jwt_secret,
        org_id=str(store.org), roles=["viewer"])
    r = await store.client.post(
        "/v1/campaigns", headers={"Authorization": f"Bearer {viewer}"}, json={"name": "x"})
    assert r.status_code == 403
    assert "not included in this plan" not in r.text  # refused by RBAC, not entitlement


async def test_entitlement_denial_wins_even_for_an_owner(store: Store) -> None:
    await store.set_capabilities([])
    r = await store.client.post("/v1/campaigns", headers=store.headers, json={"name": "x"})
    assert _denied(r)
