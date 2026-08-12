"""Landing-page API (LP-1) end-to-end: create → preview (real jewelry HTML) → public CTA → event,
plus tenant isolation, validation and authz. Against real Postgres; skips when DB unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

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


def _owner(user: uuid.UUID, org: uuid.UUID) -> dict[str, str]:
    token = auth.issue_access_token(
        sub=str(user), secret=get_settings().jwt_secret, org_id=str(org), roles=["owner"])
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Scene:
    client: httpx.AsyncClient
    org_a: uuid.UUID
    owner_a: uuid.UUID
    org_b: uuid.UUID
    owner_b: uuid.UUID
    tag: str


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/landing not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org_a, owner_a, org_b, owner_b = (uuid.uuid4() for _ in range(4))
    tag = org_a.hex[:8]
    conn = await asyncpg.connect(_dsn())
    try:
        for org, owner, suffix in ((org_a, owner_a, "A"), (org_b, owner_b, "B")):
            # vertical defaults to 'jewelry' (migration 002) → the jewelry landing template applies.
            await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,$2)",
                               org, f"LPStore-{tag}-{suffix}")
            await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                               owner, f"own+{owner.hex[:8]}@t.test")
            await conn.execute(
                "INSERT INTO user_orgs (user_id, org_id, role) VALUES ($1,$2,'owner')", owner, org)
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, org_a, owner_a, org_b, owner_b, tag)
    conn = await asyncpg.connect(_dsn())
    try:  # org delete cascades landing_pages + versions + events
        await conn.execute("DELETE FROM organizations WHERE name LIKE $1", f"LPStore-{tag}%")
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", [owner_a, owner_b])
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _body(**over: object) -> dict:
    base = {"slug": "diwali-diamond", "headline": "Everyday Diamond Pendants",
            "offer": "Starting at ₹29,999", "subheadline": "Certified & hallmarked.",
            "objective": "whatsapp",
            "products": [{"title": "Solitaire Pendant", "price_text": "₹29,999"},
                         {"title": "Halo Pendant", "price_text": "₹42,500"}]}
    base.update(over)
    return base


async def _events(page_id: str) -> list[str]:
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            "SELECT type FROM landing_page_events WHERE page_id=$1::uuid", page_id)
        return [r["type"] for r in rows]
    finally:
        await conn.close()


async def _event_row(page_id: str, type_: str) -> dict:
    conn = await asyncpg.connect(_dsn())
    try:
        r = await conn.fetchrow(
            "SELECT item_ref, variant, utm, meta FROM landing_page_events "
            "WHERE page_id=$1::uuid AND type=$2 LIMIT 1", page_id, type_)
        assert r is not None, f"no {type_} row"
        return {"item_ref": r["item_ref"], "variant": r["variant"],
                "utm": json.loads(r["utm"]) if isinstance(r["utm"], str) else r["utm"],
                "meta": json.loads(r["meta"]) if isinstance(r["meta"], str) else r["meta"]}
    finally:
        await conn.close()


async def test_create_preview_and_cta_event(scene: Scene) -> None:
    # 1. Create the page (deterministic plan → validate → persist a version).
    r = await scene.client.post(
        "/v1/landing/pages", headers=_owner(scene.owner_a, scene.org_a), json=_body())
    assert r.status_code == 201, r.text
    page_id = r.json()["page_id"]
    assert r.json()["preview_url"].endswith(f"{page_id}/preview")

    # 2. Preview renders a real tenant-branded jewelry page.
    p = await scene.client.get(
        f"/v1/landing/pages/{page_id}/preview", headers=_owner(scene.owner_a, scene.org_a))
    assert p.status_code == 200 and p.headers["content-type"].startswith("text/html")
    body = p.text
    assert "Everyday Diamond Pendants" in body and "Enquire on WhatsApp" in body
    assert "BIS Hallmarked" in body  # jewelry trust signal from the pack
    assert 'content="noindex' in body  # paid page not indexed

    # 3. A public CTA beacon records a Growth Operator event (tenant resolved from page_id).
    t = await scene.client.post(
        "/v1/landing/track", json={"page_id": page_id, "type": "landing_page.cta_clicked"})
    assert t.status_code == 204
    assert await _events(page_id) == ["landing_page.cta_clicked"]

    # a version row exists with the embedded experience_strategy + spec
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT experience_strategy, spec FROM landing_page_versions WHERE page_id=$1::uuid",
            page_id)
        assert row is not None and row["experience_strategy"] and row["spec"]
    finally:
        await conn.close()


async def test_preview_is_tenant_isolated(scene: Scene) -> None:
    page_id = (await scene.client.post(
        "/v1/landing/pages", headers=_owner(scene.owner_a, scene.org_a),
        json=_body())).json()["page_id"]
    # Owner B cannot preview org A's page (RLS scopes to the caller's org → 404).
    r = await scene.client.get(
        f"/v1/landing/pages/{page_id}/preview", headers=_owner(scene.owner_b, scene.org_b))
    assert r.status_code == 404


async def test_bad_input_is_422(scene: Scene) -> None:
    bad_slug = await scene.client.post(
        "/v1/landing/pages", headers=_owner(scene.owner_a, scene.org_a),
        json=_body(slug="Not A Slug!"))
    assert bad_slug.status_code == 422


async def test_track_unknown_page_or_type_records_nothing(scene: Scene) -> None:
    # unknown page → 204 (never leaks existence), nothing recorded
    ghost = uuid.uuid4()
    assert (await scene.client.post(
        "/v1/landing/track",
        json={"page_id": str(ghost), "type": "landing_page.viewed"})).status_code == 204
    assert await _events(str(ghost)) == []
    # disallowed event type on a real page → 204, nothing recorded
    page_id = (await scene.client.post(
        "/v1/landing/pages", headers=_owner(scene.owner_a, scene.org_a),
        json=_body())).json()["page_id"]
    assert (await scene.client.post(
        "/v1/landing/track",
        json={"page_id": page_id, "type": "landing_page.hacked"})).status_code == 204
    assert await _events(page_id) == []


async def test_create_requires_campaign_permission(scene: Scene) -> None:
    # a viewer (no campaigns:send) cannot create a page
    viewer = auth.issue_access_token(
        sub=str(uuid.uuid4()), secret=get_settings().jwt_secret,
        org_id=str(scene.org_a), roles=["viewer"])
    r = await scene.client.post(
        "/v1/landing/pages", headers={"Authorization": f"Bearer {viewer}"}, json=_body())
    assert r.status_code == 403


# ---- LP-1b: per-item data capture + insights -------------------------------------------------

async def _track(client: httpx.AsyncClient, **body: object) -> int:
    return (await client.post("/v1/landing/track", json=body)).status_code


async def test_item_capture_persists_and_ranks_by_interest(scene: Scene) -> None:
    page_id = (await scene.client.post(
        "/v1/landing/pages", headers=_owner(scene.owner_a, scene.org_a),
        json=_body())).json()["page_id"]

    # a customer engages: solitaire clicked twice, halo once, plus views + a CTA
    for _ in range(2):
        assert await _track(scene.client, page_id=page_id, type="landing_page.item_clicked",
                            item_ref="solitaire-pendant", session_id="s1", variant="default",
                            utm={"source": "instagram", "campaign": "diwali"},
                            meta={"section": "product_grid", "device": "mobile",
                                  "scroll": 60, "dwell": 25}) == 204
    assert await _track(scene.client, page_id=page_id, type="landing_page.item_clicked",
                        item_ref="halo-pendant") == 204
    assert await _track(scene.client, page_id=page_id, type="landing_page.item_viewed",
                        item_ref="solitaire-pendant") == 204
    assert await _track(scene.client, page_id=page_id, type="landing_page.cta_clicked",
                        item_ref="solitaire-pendant") == 204

    # the rich context bundle is persisted on the item_clicked row
    row = await _event_row(page_id, "landing_page.item_clicked")
    assert row["item_ref"] == "solitaire-pendant" and row["variant"] == "default"
    assert row["utm"]["source"] == "instagram"
    assert row["meta"]["section"] == "product_grid" and row["meta"]["scroll"] == 60

    # the owner's insight: which item is most wanted
    r = await scene.client.get(
        f"/v1/landing/pages/{page_id}/insights", headers=_owner(scene.owner_a, scene.org_a))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["top_items"][0] == {"item_ref": "solitaire-pendant", "clicks": 2, "views": 1}
    assert data["top_items"][1]["item_ref"] == "halo-pendant"
    assert data["events"]["landing_page.item_clicked"] == 3


async def test_insights_is_tenant_isolated(scene: Scene) -> None:
    page_id = (await scene.client.post(
        "/v1/landing/pages", headers=_owner(scene.owner_a, scene.org_a),
        json=_body())).json()["page_id"]
    r = await scene.client.get(
        f"/v1/landing/pages/{page_id}/insights", headers=_owner(scene.owner_b, scene.org_b))
    assert r.status_code == 404  # org B cannot read org A's analytics


async def test_track_clamps_untrusted_body(scene: Scene) -> None:
    page_id = (await scene.client.post(
        "/v1/landing/pages", headers=_owner(scene.owner_a, scene.org_a),
        json=_body())).json()["page_id"]
    # a hostile beacon: overlong item_ref, junk/oversized meta + utm keys, absurd numbers
    assert await _track(
        scene.client, page_id=page_id, type="landing_page.item_clicked",
        item_ref="x" * 500, utm={"source": "y" * 500, "evil": "drop"},
        meta={"section": "z" * 500, "scroll": 10 ** 12, "evil": "payload",
              "device": "mobile"}) == 204
    row = await _event_row(page_id, "landing_page.item_clicked")
    assert len(row["item_ref"]) <= 64
    assert set(row["meta"]).issubset({"section", "device", "referrer", "scroll", "dwell"})
    assert "evil" not in row["meta"] and "evil" not in row["utm"]
    assert len(row["meta"]["section"]) <= 120 and row["meta"]["scroll"] <= 100000
    assert len(row["utm"]["source"]) <= 120
