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
from core.landing import api as landing_api
from core.landing import ratelimit
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
    ratelimit.reset()  # LP-3a: isolate the in-process rate-limiter across tests
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
        # audit_log is append-only (immutable trigger) → clear the orgs' rows first (owner conn).
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute(
            "DELETE FROM audit_log WHERE org_id = ANY($1::uuid[])", [org_a, org_b])
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
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


# ---- LP-2a: multi-variant generation -----------------------------------------------------------

async def _versions(page_id: str) -> list[dict]:
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            "SELECT version_no, variant_label FROM landing_page_versions "
            "WHERE page_id=$1::uuid ORDER BY version_no", page_id)
        return [{"version_no": r["version_no"], "variant_label": r["variant_label"]} for r in rows]
    finally:
        await conn.close()


async def test_generate_three_variants_and_preview_each(scene: Scene) -> None:
    r = await scene.client.post(
        "/v1/landing/pages", headers=_owner(scene.owner_a, scene.org_a),
        json=_body(variants=3))
    assert r.status_code == 201, r.text
    page_id = r.json()["page_id"]
    variants = r.json()["variants"]
    assert [v["variant_label"] for v in variants] == ["classic", "focused", "story"]
    assert all(v["preview_url"].endswith(f"/versions/{v['version_no']}/preview") for v in variants)

    # 3 immutable version rows persisted
    assert await _versions(page_id) == [
        {"version_no": 1, "variant_label": "classic"},
        {"version_no": 2, "variant_label": "focused"},
        {"version_no": 3, "variant_label": "story"}]

    # the list endpoint agrees
    lst = await scene.client.get(
        f"/v1/landing/pages/{page_id}/variants", headers=_owner(scene.owner_a, scene.org_a))
    assert lst.status_code == 200 and len(lst.json()) == 3

    # each variant previews as real, DIFFERENT HTML (focused drops testimonials; classic keeps them)
    bodies = {}
    for v in variants:
        p = await scene.client.get(
            f"/v1/landing/pages/{page_id}/versions/{v['version_no']}/preview",
            headers=_owner(scene.owner_a, scene.org_a))
        assert p.status_code == 200 and "Everyday Diamond Pendants" in p.text
        bodies[v["variant_label"]] = p.text
    assert len({*bodies.values()}) == 3  # three genuinely different pages
    assert "lp-testimonials" not in bodies["focused"]  # focused trims social proof
    assert "lp-quotes" in bodies["classic"]            # classic keeps it


async def _version_planner(page_id: str, version_no: int) -> str:
    conn = await asyncpg.connect(_dsn())
    try:
        sc = await conn.fetchval(
            "SELECT source_context FROM landing_page_versions "
            "WHERE page_id=$1::uuid AND version_no=$2", page_id, version_no)
        return (json.loads(sc) if isinstance(sc, str) else sc)["planner"]
    finally:
        await conn.close()


async def test_use_llm_falls_back_to_deterministic_when_provider_off(scene: Scene) -> None:
    # the owner asks for LLM-planned variants, but no provider is wired (default) → safe fallback:
    # deterministic archetypes, no network, and the provenance says so.
    r = await scene.client.post(
        "/v1/landing/pages", headers=_owner(scene.owner_a, scene.org_a),
        json=_body(variants=3, use_llm=True))
    assert r.status_code == 201
    assert [v["variant_label"] for v in r.json()["variants"]] == ["classic", "focused", "story"]
    assert await _version_planner(r.json()["page_id"], 1) == "deterministic"


async def test_single_variant_is_backward_compatible(scene: Scene) -> None:
    r = await scene.client.post(
        "/v1/landing/pages", headers=_owner(scene.owner_a, scene.org_a), json=_body())
    assert r.status_code == 201
    assert r.json()["variants"] == [
        {"version_no": 1, "variant_label": "default",
         "preview_url": r.json()["preview_url"].replace("/preview", "/versions/1/preview")}]
    assert len(await _versions(r.json()["page_id"])) == 1


async def test_variant_preview_is_tenant_isolated(scene: Scene) -> None:
    page_id = (await scene.client.post(
        "/v1/landing/pages", headers=_owner(scene.owner_a, scene.org_a),
        json=_body(variants=3))).json()["page_id"]
    # org B cannot list or preview org A's variants
    assert (await scene.client.get(
        f"/v1/landing/pages/{page_id}/variants",
        headers=_owner(scene.owner_b, scene.org_b))).status_code == 404
    assert (await scene.client.get(
        f"/v1/landing/pages/{page_id}/versions/2/preview",
        headers=_owner(scene.owner_b, scene.org_b))).status_code == 404
    # a non-existent version → 404 (own org)
    assert (await scene.client.get(
        f"/v1/landing/pages/{page_id}/versions/9/preview",
        headers=_owner(scene.owner_a, scene.org_a))).status_code == 404


# ---- LP-2b: lifecycle + owner approval (HITL #1) ------------------------------------------------

async def _make_page(scene: Scene, *, variants: int = 3) -> str:
    return (await scene.client.post(
        "/v1/landing/pages", headers=_owner(scene.owner_a, scene.org_a),
        json=_body(variants=variants))).json()["page_id"]


async def _detail(scene: Scene, page_id: str, owner=None, org=None) -> httpx.Response:
    return await scene.client.get(
        f"/v1/landing/pages/{page_id}",
        headers=_owner(owner or scene.owner_a, org or scene.org_a))


async def _act(scene: Scene, page_id: str, action: str, *, json=None, owner=None,
               org=None) -> httpx.Response:
    return await scene.client.post(
        f"/v1/landing/pages/{page_id}/{action}",
        headers=_owner(owner or scene.owner_a, org or scene.org_a), json=json)


async def _transition_audits(page_id: str) -> int:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM audit_log WHERE resource=$1 AND action='landing_page.transition'",
            page_id)
    finally:
        await conn.close()


async def test_lifecycle_happy_path_and_is_audited(scene: Scene) -> None:
    page_id = await _make_page(scene)
    assert (await _detail(scene, page_id)).json()["status"] == "generated"

    # owner approves + selects the "focused" candidate (version 2) — HITL #1
    r = await _act(scene, page_id, "select", json={"version_no": 2})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    d = (await _detail(scene, page_id)).json()
    assert d["current_version_no"] == 2 and d["current_variant_label"] == "focused"

    assert (await _act(scene, page_id, "publish")).json()["status"] == "published"
    assert (await _detail(scene, page_id)).json()["status"] == "published"
    assert (await _act(scene, page_id, "pause")).json()["status"] == "paused"
    assert (await _act(scene, page_id, "publish")).json()["status"] == "published"
    assert (await _act(scene, page_id, "archive")).json()["status"] == "archived"

    # every transition left an immutable audit record (select, publish, pause, publish, archive)
    assert await _transition_audits(page_id) == 5


async def test_publish_before_approval_is_409(scene: Scene) -> None:
    page_id = await _make_page(scene)
    r = await _act(scene, page_id, "publish")  # still 'generated'
    assert r.status_code == 409
    assert (await _detail(scene, page_id)).json()["status"] == "generated"  # unchanged
    assert await _transition_audits(page_id) == 0  # a rejected transition writes nothing


async def test_illegal_transition_is_409(scene: Scene) -> None:
    page_id = await _make_page(scene)
    await _act(scene, page_id, "select", json={"version_no": 1})  # → approved
    assert (await _act(scene, page_id, "pause")).status_code == 409  # can't pause an approved page


async def test_rollback_repoints_to_earlier_variant(scene: Scene) -> None:
    page_id = await _make_page(scene)
    await _act(scene, page_id, "select", json={"version_no": 3})   # story
    await _act(scene, page_id, "publish")
    r = await _act(scene, page_id, "rollback", json={"version_no": 1})  # back to classic
    assert r.status_code == 200 and r.json()["status"] == "approved"
    d = (await _detail(scene, page_id)).json()
    assert d["current_version_no"] == 1 and d["current_variant_label"] == "classic"


async def test_select_unknown_version_is_404(scene: Scene) -> None:
    page_id = await _make_page(scene)
    assert (await _act(scene, page_id, "select", json={"version_no": 9})).status_code == 404


async def test_lifecycle_is_tenant_isolated(scene: Scene) -> None:
    page_id = await _make_page(scene)
    # org B cannot see, select, or publish org A's page
    assert (await _detail(scene, page_id, owner=scene.owner_b,
                          org=scene.org_b)).status_code == 404
    assert (await _act(scene, page_id, "select", json={"version_no": 1},
                       owner=scene.owner_b, org=scene.org_b)).status_code == 404
    assert (await _act(scene, page_id, "publish", owner=scene.owner_b,
                       org=scene.org_b)).status_code == 404
    assert (await _detail(scene, page_id)).json()["status"] == "generated"  # A's page untouched


async def test_lifecycle_requires_campaign_permission(scene: Scene) -> None:
    page_id = await _make_page(scene)
    viewer = auth.issue_access_token(
        sub=str(uuid.uuid4()), secret=get_settings().jwt_secret,
        org_id=str(scene.org_a), roles=["viewer"])
    h = {"Authorization": f"Bearer {viewer}"}
    assert (await scene.client.post(
        f"/v1/landing/pages/{page_id}/select", headers=h,
        json={"version_no": 1})).status_code == 403
    assert (await scene.client.post(
        f"/v1/landing/pages/{page_id}/publish", headers=h)).status_code == 403


# ---- LP-3a: public serving surface -------------------------------------------------------------

async def _publish_page(scene: Scene) -> str:
    """Generate 3 variants → owner selects one → publish → return the page id (now live)."""
    page_id = await _make_page(scene)
    await _act(scene, page_id, "select", json={"version_no": 1})
    await _act(scene, page_id, "publish")
    return page_id


async def test_serve_published_page_public(scene: Scene) -> None:
    page_id = await _publish_page(scene)
    r = await scene.client.get(f"/p/{page_id}")  # NO auth header — public
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "Everyday Diamond Pendants" in r.text and "Enquire on WhatsApp" in r.text
    assert 'content="noindex' in r.text  # the page's own meta
    # HTTP security headers reinforce it
    assert r.headers["x-robots-tag"] == "noindex, nofollow"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "strict-origin" in r.headers["referrer-policy"]


async def test_only_published_pages_are_served(scene: Scene) -> None:
    page_id = await _make_page(scene)  # 'generated'
    assert (await scene.client.get(f"/p/{page_id}")).status_code == 404
    await _act(scene, page_id, "select", json={"version_no": 1})  # 'approved' (not published)
    assert (await scene.client.get(f"/p/{page_id}")).status_code == 404
    await _act(scene, page_id, "publish")  # now live
    assert (await scene.client.get(f"/p/{page_id}")).status_code == 200
    await _act(scene, page_id, "pause")  # taken down → no longer served
    assert (await scene.client.get(f"/p/{page_id}")).status_code == 404


async def test_serve_unknown_page_is_404(scene: Scene) -> None:
    assert (await scene.client.get(f"/p/{uuid.uuid4()}")).status_code == 404


async def test_serve_is_rate_limited(scene: Scene, monkeypatch: pytest.MonkeyPatch) -> None:
    page_id = await _publish_page(scene)
    ratelimit.reset()
    monkeypatch.setattr(landing_api, "SERVE_PER_MIN", 3)
    for _ in range(3):
        assert (await scene.client.get(f"/p/{page_id}")).status_code == 200
    assert (await scene.client.get(f"/p/{page_id}")).status_code == 429  # 4th over the cap


async def test_track_flood_is_silently_dropped(
    scene: Scene, monkeypatch: pytest.MonkeyPatch) -> None:
    page_id = await _publish_page(scene)
    ratelimit.reset()
    monkeypatch.setattr(landing_api, "TRACK_PER_MIN", 2)
    for _ in range(2):
        assert await _track(scene.client, page_id=page_id, type="landing_page.viewed") == 204
    # 3rd is over the cap → still 204 (never a 429 that leaks a signal), but records nothing
    assert await _track(scene.client, page_id=page_id, type="landing_page.viewed") == 204
    assert await _events(page_id) == ["landing_page.viewed", "landing_page.viewed"]
