"""Public landing runtime is an ongoing paid service (PLAN-5).

Serving a live page, collecting funnel events and capturing new leads are all work Growth Operator
performs on the merchant's behalf — not reads of records they already own. They therefore require a
current `landing_pages` grant. Every denial is **neutral**: the public surface must never disclose
that a merchant's subscription lapsed, so an unentitled page is indistinguishable from a draft or an
unknown one.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy import auth


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.landing_pages')"))
    finally:
        await conn.close()


class Pub:
    def __init__(self, conn: asyncpg.Connection, tag: str, client: httpx.AsyncClient) -> None:
        self.conn, self.tag, self.client = conn, tag, client
        self.org = uuid.uuid4()
        self.other = uuid.uuid4()
        self.user = uuid.uuid4()
        self.plan = uuid.uuid4()
        self.page: uuid.UUID | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {auth.issue_access_token(
            sub=str(self.user), secret=get_settings().jwt_secret,
            org_id=str(self.org), roles=['owner'])}"}

    async def setup(self) -> None:
        for oid, name in ((self.org, "A"), (self.other, "B")):
            await self.conn.execute(
                "INSERT INTO organizations (id, name, vertical) VALUES ($1,$2,'jewelry')",
                oid, f"{self.tag}-{name}")
        await self.conn.execute(
            "INSERT INTO users (id, email) VALUES ($1,$2)", self.user, f"{self.tag}@t.test")
        await self.conn.execute(
            "INSERT INTO user_orgs (user_id, org_id, role) VALUES ($1,$2,'owner')",
            self.user, self.org)
        await self.conn.execute(
            "INSERT INTO billing_plans (id, name, price_minor, features, config) "
            "VALUES ($1,$2,1,'[]'::jsonb,$3::jsonb)",
            self.plan, f"{self.tag}-plan",
            json.dumps({"entitlement_schema_version": 1,
                        "entitlements": ["catalog", "customers", "landing_pages"],
                        "agents": [], "channels": [], "addons": [], "promotions": [],
                        "vertical": None}))
        await self.conn.execute(
            "INSERT INTO billing_subscriptions (org_id, plan_id, status) VALUES ($1,$2,'active')",
            self.org, self.plan)
        self.page = await self._publish(self.org, f"slug-{self.tag}")

    async def _publish(self, org: uuid.UUID, slug: str) -> uuid.UUID:
        """A real published page: the spec comes from the deterministic planner, so the public
        route renders exactly as it would in production."""
        from core.landing.plan import CampaignContext, plan_page
        from core.landing.spec import BrandTokens

        strategy, spec = plan_page(
            CampaignContext(headline="Hello", offer="Offer", wa_number="+910000000000"),
            BrandTokens(), "jewelry")
        page = await self.conn.fetchval(
            "INSERT INTO landing_pages (org_id, vertical, slug, status, conversion_goal) "
            "VALUES ($1,'jewelry',$2,'published','whatsapp') RETURNING id", org, slug)
        version = await self.conn.fetchval(
            "INSERT INTO landing_page_versions (page_id, org_id, version_no, "
            "experience_strategy, spec, source_context, asset_provenance, variant_label, "
            "published_at) VALUES ($1,$2,1,$3::jsonb,$4::jsonb,'{}'::jsonb,'{}'::jsonb,'a',now()) "
            "RETURNING id",
            page, org, json.dumps(strategy.to_dict()),
            json.dumps(spec.to_dict()))
        await self.conn.execute(
            "UPDATE landing_pages SET current_version_id = $2 WHERE id = $1", page, version)
        return page

    async def revoke(self) -> None:
        await self.conn.execute(
            "UPDATE billing_plans SET config = jsonb_set(config, '{entitlements}', "
            "'[\"catalog\",\"customers\"]'::jsonb) WHERE id = $1", self.plan)

    async def restore(self) -> None:
        await self.conn.execute(
            "UPDATE billing_plans SET config = jsonb_set(config, '{entitlements}', "
            "'[\"catalog\",\"customers\",\"landing_pages\"]'::jsonb) WHERE id = $1", self.plan)

    async def page_status(self) -> str:
        return await self.conn.fetchval(
            "SELECT status FROM landing_pages WHERE id = $1", self.page)

    async def event_count(self) -> int:
        return await self.conn.fetchval(
            "SELECT count(*) FROM landing_page_events WHERE page_id = $1", self.page)

    async def lead_count(self) -> int:
        return await self.conn.fetchval(
            "SELECT count(*) FROM leads WHERE org_id = $1", self.org)

    async def contact_count(self) -> int:
        return await self.conn.fetchval(
            "SELECT count(*) FROM contacts WHERE org_id = $1", self.org)


@pytest.fixture()
async def pub() -> AsyncIterator[Pub]:
    if not await _db_ready():
        pytest.skip("Postgres/landing not ready")
    from core.api.main import app
    from core.landing import ratelimit

    ratelimit.reset()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    conn = await asyncpg.connect(_dsn())
    tag = f"lpub-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        p = Pub(conn, tag, client)
        await p.setup()
        try:
            yield p
        finally:
            await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
            await conn.execute(
                "DELETE FROM audit_log WHERE org_id = ANY($1::uuid[])", [p.org, p.other])
            await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
            await conn.execute("DELETE FROM billing_subscriptions WHERE org_id=$1", p.org)
            await conn.execute("DELETE FROM user_orgs WHERE org_id=$1", p.org)
            await conn.execute(
                "DELETE FROM organizations WHERE id = ANY($1::uuid[])", [p.org, p.other])
            await conn.execute("DELETE FROM users WHERE id=$1", p.user)
            await conn.execute("DELETE FROM billing_plans WHERE id=$1", p.plan)
            await conn.close()
            await dbmod.get_engine().dispose()
            dbmod.get_engine.cache_clear()
            dbmod.get_sessionmaker.cache_clear()


LEAD_BODY = {"phone": "+919876500011", "consent": True, "name": "A"}


# ---- Entitled: the hosted service works --------------------------------------------------------


async def test_an_entitled_page_is_served(pub: Pub) -> None:
    r = await pub.client.get(f"/p/{pub.page}")
    assert r.status_code == 200


# ---- Unentitled: neutral denial, nothing recorded ---------------------------------------------


async def test_public_page_not_served(pub: Pub) -> None:
    """Same neutral outcome as a draft or unknown page — never 'subscription expired'."""
    await pub.revoke()
    r = await pub.client.get(f"/p/{pub.page}")
    assert r.status_code == 404
    body = r.text.lower()
    for leak in ("subscription", "plan", "billing", "entitle", "expired", "payment"):
        assert leak not in body, f"public response leaked {leak!r}"


async def test_an_unknown_page_looks_identical_to_an_unentitled_one(pub: Pub) -> None:
    unknown = await pub.client.get(f"/p/{uuid.uuid4()}")
    await pub.revoke()
    unentitled = await pub.client.get(f"/p/{pub.page}")
    assert unknown.status_code == unentitled.status_code == 404


async def test_track_records_nothing(pub: Pub) -> None:
    await pub.revoke()
    r = await pub.client.post(
        "/v1/landing/track", json={"page_id": str(pub.page), "type": "view"})
    assert r.status_code == 204            # neutral, unchanged shape
    assert await pub.event_count() == 0


async def test_lead_not_captured(pub: Pub) -> None:
    """No lead and no contact — an unentitled store must not accumulate new PII."""
    await pub.revoke()
    before_leads, before_contacts = await pub.lead_count(), await pub.contact_count()
    r = await pub.client.post(f"/p/{pub.page}/lead", json=LEAD_BODY)
    assert r.status_code in (200, 202, 204, 404)
    assert "subscription" not in r.text.lower() and "plan" not in r.text.lower()
    assert await pub.lead_count() == before_leads
    assert await pub.contact_count() == before_contacts


async def test_denial_does_not_rewrite_lifecycle_state(pub: Pub) -> None:
    await pub.revoke()
    await pub.client.get(f"/p/{pub.page}")
    assert await pub.page_status() == "published"


async def test_restoring_entitlement_restores_serving(pub: Pub) -> None:
    await pub.revoke()
    assert (await pub.client.get(f"/p/{pub.page}")).status_code == 404
    await pub.restore()
    assert (await pub.client.get(f"/p/{pub.page}")).status_code == 200
    assert await pub.page_status() == "published"     # never mutated by either transition


# ---- Owner-side continuity ---------------------------------------------------------------------


async def test_owner_still_reads_landing_records_after_revocation(pub: Pub) -> None:
    """The merchant keeps visibility of pages they already own; only the hosted runtime stops."""
    await pub.revoke()
    listing = await pub.client.get("/v1/landing/pages", headers=pub.headers)
    assert listing.status_code == 200
    detail = await pub.client.get(f"/v1/landing/pages/{pub.page}", headers=pub.headers)
    assert detail.status_code == 200


# ---- Isolation ---------------------------------------------------------------------------------


async def test_entitlement_is_checked_against_the_owning_org(pub: Pub) -> None:
    """A page id resolves its own org through the SECURITY DEFINER function, so another tenant's
    subscription can never decide whether this page is served."""
    other_page = await pub._publish(pub.other, f"other-{pub.tag}")
    # `pub.org` stays entitled; the other org has no subscription at all.
    assert (await pub.client.get(f"/p/{pub.page}")).status_code == 200
    assert (await pub.client.get(f"/p/{other_page}")).status_code == 404
