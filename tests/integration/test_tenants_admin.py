"""`GET /v1/admin/tenants` — the operator cross-store roster (Phase 4, P4.1) against real Postgres.

Proves the roster (a) reflects real per-store state (paused flag + open-ticket + member counts, via
the `platform_tenant_roster()` SECURITY DEFINER function), (b) exposes ONLY curated registry/count
fields and never customer PII, and (c) is properly gated: 403 for a non-operator, 401 without a
token, 404 when the operator plane is disabled. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

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
        return bool(await conn.fetchval("SELECT to_regprocedure('platform_tenant_roster()')"))
    finally:
        await conn.close()


def _bearer(user: uuid.UUID) -> dict[str, str]:
    token = issue_access_token(
        sub=str(user), secret=get_settings().jwt_secret, org_id=None, roles=[])
    return {"Authorization": f"Bearer {token}"}


# The complete, curated set the roster is allowed to expose — asserted exactly, so any future column
# that leaks customer data fails the test.
_CURATED_FIELDS = {
    "org_id", "name", "plan", "status", "created_at", "paused", "open_tickets", "member_count",
}
_FORBIDDEN_SUBSTRINGS = ("phone", "email", "contact", "message", "revenue", "address")


@dataclass
class Scene:
    client: httpx.AsyncClient
    operator: uuid.UUID
    org_id: uuid.UUID


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/platform_tenant_roster not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    operator, store_user, org_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           operator, f"op+{operator.hex[:8]}@example.test")
        await conn.execute("INSERT INTO platform_admins (user_id, role) VALUES ($1,'admin')",
                           operator)
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           store_user, f"owner+{store_user.hex[:8]}@example.test")
        await conn.execute(
            "INSERT INTO organizations (id, name, status, plan) "
            "VALUES ($1,'Roster Store','active','pilot')", org_id)
        await conn.execute("INSERT INTO user_orgs (user_id, org_id) VALUES ($1,$2)",
                           store_user, org_id)
        # paused = true (jsonb) + one OPEN ticket → both must show up in the roster row.
        await conn.execute(
            "INSERT INTO tenant_settings (org_id, key, value) "
            "VALUES ($1,'autonomy.paused',$2::jsonb)", org_id, "true")
        await conn.execute(
            "INSERT INTO support_tickets (org_id, subject, description) VALUES ($1,'q','d')",
            org_id)
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, operator, org_id)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM tenant_settings WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM support_tickets WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM user_orgs WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org_id)
        await conn.execute(
            "ALTER TABLE platform_access_log DISABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_access_log WHERE actor_user_id=$1", operator)
        await conn.execute(
            "ALTER TABLE platform_access_log ENABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", operator)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", [operator, store_user])
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_roster_reflects_real_store_state(scene: Scene) -> None:
    r = await scene.client.get("/v1/admin/tenants", headers=_bearer(scene.operator))
    assert r.status_code == 200, r.text
    row = next((x for x in r.json() if x["org_id"] == str(scene.org_id)), None)
    assert row is not None, "seeded store missing from the roster"
    assert row["paused"] is True          # autonomy.paused=true reflected
    assert row["open_tickets"] == 1       # the one OPEN ticket counted
    assert row["member_count"] == 1       # the one membership counted
    assert row["status"] == "active" and row["plan"] == "pilot"


async def test_roster_exposes_only_curated_fields_no_pii(scene: Scene) -> None:
    r = await scene.client.get("/v1/admin/tenants", headers=_bearer(scene.operator))
    assert r.status_code == 200
    body = r.json()
    assert body, "expected at least the seeded store"
    for row in body:
        extra = set(row) - _CURATED_FIELDS
        assert set(row.keys()) == _CURATED_FIELDS, f"unexpected fields: {extra}"
        blob = " ".join(str(k) for k in row).lower()
        for bad in _FORBIDDEN_SUBSTRINGS:
            assert bad not in blob  # no customer-PII-shaped field ever appears


async def test_roster_403_for_non_operator(scene: Scene) -> None:
    # A valid token for a user who is NOT on the platform allowlist → 403 (no cross-store data).
    r = await scene.client.get("/v1/admin/tenants", headers=_bearer(uuid.uuid4()))
    assert r.status_code == 403


async def test_roster_401_without_token(scene: Scene) -> None:
    r = await scene.client.get("/v1/admin/tenants")
    assert r.status_code == 401


async def test_roster_404_when_plane_disabled(
    scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "false")  # override the fixture
    r = await scene.client.get("/v1/admin/tenants", headers=_bearer(scene.operator))
    assert r.status_code == 404  # even a real operator can't reach a disabled plane


# ---- CP-8: per-store lead roster (where each lead was captured from) ----------------------------

async def _seed_leads(org: uuid.UUID) -> None:
    """Leads from three different origins — a landing page, WhatsApp, and word of mouth."""
    conn = await asyncpg.connect(_dsn())
    try:
        ct = await conn.fetchval(
            "INSERT INTO contacts (org_id, phone, full_name) VALUES ($1,'919000012345','Priya') "
            "RETURNING id", org)
        ch = await conn.fetchval(
            "INSERT INTO channels (org_id,type,external_id,credentials_ref) "
            "VALUES ($1,'whatsapp',$2,'ref') RETURNING id", org, f"ext-{uuid.uuid4()}")
        page = await conn.fetchval(
            "INSERT INTO landing_pages (org_id, vertical, slug, status, conversion_goal) "
            "VALUES ($1,'jewelry','diwali-diamond','published','whatsapp') RETURNING id", org)
        await conn.execute(
            "INSERT INTO leads (org_id, contact_id, source, stage, landing_page_id, variant) "
            "VALUES ($1,$2,'landing_page','new',$3,'story')", org, ct, page)
        await conn.execute(
            "INSERT INTO leads (org_id, contact_id, source, stage, channel_id) "
            "VALUES ($1,$2,'whatsapp','new',$3)", org, ct, ch)
        await conn.execute(
            "INSERT INTO leads (org_id, contact_id, source, stage) VALUES ($1,$2,'walk_in','new')",
            org, ct)
    finally:
        await conn.close()


async def test_store_leads_roster_shows_where_each_lead_came_from(scene: Scene) -> None:
    await _seed_leads(scene.org_id)
    r = await scene.client.get(
        f"/v1/admin/tenants/{scene.org_id}/leads", headers=_bearer(scene.operator))
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 3
    captured = {row["captured_from"] for row in rows}
    assert "Landing page · diwali-diamond (story)" in captured  # which page AND variant
    assert "WhatsApp" in captured and "Walk-in" in captured

    landing = next(x for x in rows if x["source"] == "landing_page")
    assert landing["landing_slug"] == "diwali-diamond" and landing["variant"] == "story"
    assert landing["contact_name"] == "Priya"


async def test_store_leads_roster_masks_customer_pii(scene: Scene) -> None:
    await _seed_leads(scene.org_id)
    r = await scene.client.get(
        f"/v1/admin/tenants/{scene.org_id}/leads", headers=_bearer(scene.operator))
    body = r.text
    assert "919000012345" not in body  # the full phone is NEVER sent to the operator console
    assert "2345" in body              # only the last 4, masked
    for row in r.json():
        assert row["contact_phone_masked"] == "••••2345"
        assert "email" not in row      # email is not exposed at all


async def test_store_leads_roster_is_scoped_to_one_store(scene: Scene) -> None:
    await _seed_leads(scene.org_id)
    other = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id,name) VALUES ($1,'OtherStore')", other)
        ct = await conn.fetchval(
            "INSERT INTO contacts (org_id, phone) VALUES ($1,'919888877777') RETURNING id", other)
        await conn.execute(
            "INSERT INTO leads (org_id, contact_id, source, stage) VALUES ($1,$2,'referral','new')",
            other, ct)
    finally:
        await conn.close()
    try:
        r = await scene.client.get(
            f"/v1/admin/tenants/{scene.org_id}/leads", headers=_bearer(scene.operator))
        assert r.status_code == 200
        # the other store's lead is never returned by this store's roster
        assert all(row["source"] != "referral" for row in r.json())
        assert "7777" not in r.text
    finally:
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute("DELETE FROM organizations WHERE id=$1", other)
        finally:
            await conn.close()


async def test_store_leads_roster_is_audited(scene: Scene) -> None:
    await _seed_leads(scene.org_id)
    await scene.client.get(
        f"/v1/admin/tenants/{scene.org_id}/leads", headers=_bearer(scene.operator))
    conn = await asyncpg.connect(_dsn())
    try:
        action = await conn.fetchval(
            "SELECT action FROM platform_access_log WHERE actor_user_id=$1 "
            "AND action='store.leads.read' ORDER BY created_at DESC LIMIT 1", scene.operator)
    finally:
        await conn.close()
    assert action == "store.leads.read"  # every operator read of customer data is logged


async def test_store_leads_roster_403_for_non_operator(scene: Scene) -> None:
    stranger = uuid.uuid4()
    r = await scene.client.get(
        f"/v1/admin/tenants/{scene.org_id}/leads", headers=_bearer(stranger))
    assert r.status_code == 403


async def test_store_leads_roster_404_when_plane_disabled(
    scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "false")
    r = await scene.client.get(
        f"/v1/admin/tenants/{scene.org_id}/leads", headers=_bearer(scene.operator))
    assert r.status_code == 404
