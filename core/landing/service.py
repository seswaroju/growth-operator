"""Landing-page service (LP-1): create a page+version from a campaign context, resolve
brand/vertical, render, and record funnel events. Org-scoped → tenant context set before access.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.landing.plan import CampaignContext, plan_page
from core.landing.spec import BrandTokens, LandingPageSpec
from core.landing.validate import validate_spec
from core.tenancy.repository import set_org_context

# Event types the public track beacon may record — the local funnel sink (NOT the event outbox;
# the outbox `landing_page.*` fan-out is LP-3). LP-1b adds per-item view/click.
TRACK_EVENT_TYPES = frozenset({
    "landing_page.viewed", "landing_page.cta_clicked", "landing_page.form_submitted",
    "landing_page.item_viewed", "landing_page.item_clicked"})

# The public /track body is untrusted → whitelist + clamp everything before it is persisted.
_UTM_KEYS = ("source", "medium", "campaign", "term", "content")
_META_STR_KEYS = ("section", "device", "referrer")
_META_INT_KEYS = ("scroll", "dwell")


def _clip(value: object, limit: int) -> str | None:
    if value is None:
        return None
    return str(value)[:limit]


def _clip_utm(utm: object) -> dict[str, str]:
    if not isinstance(utm, dict):
        return {}
    out: dict[str, str] = {}
    for k in _UTM_KEYS:
        v = utm.get(k)
        if isinstance(v, str) and v:
            out[k] = v[:120]
    return out


def _clip_meta(meta: object) -> dict[str, Any]:
    """Only a fixed set of first-party context keys survive; strings capped, ints bounded."""
    if not isinstance(meta, dict):
        return {}
    out: dict[str, Any] = {}
    for k in _META_STR_KEYS:
        v = meta.get(k)
        if isinstance(v, str) and v:
            out[k] = v[:120]
    for k in _META_INT_KEYS:
        v = meta.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = max(0, min(100000, int(v)))
    return out


async def resolve_brand(session: AsyncSession, org_id: UUID) -> BrandTokens:
    """Tenant brand tokens: the org name + an optional `brand` tenant-setting over the defaults."""
    name = (
        await session.execute(
            text("SELECT name FROM organizations WHERE id = :id"), {"id": str(org_id)})
    ).scalar() or "Store"
    await set_org_context(session, org_id)
    setting = (
        await session.execute(
            text("SELECT value FROM tenant_settings WHERE key = 'brand'"))
    ).scalar()
    tokens = {} if setting is None else (
        json.loads(setting) if isinstance(setting, str) else setting)
    return BrandTokens.from_dict({"name": name, **tokens})


async def org_vertical(session: AsyncSession, org_id: UUID) -> str:
    # `organizations.vertical` is NOT NULL (column default set in migration 002), so this is the
    # store's actual vertical (data, not a core literal — Rule Zero). "" only if the org is missing.
    return (
        await session.execute(
            text("SELECT vertical FROM organizations WHERE id = :id"), {"id": str(org_id)})
    ).scalar() or ""


async def create_landing_page(
    session: AsyncSession, org_id: UUID, *, campaign: CampaignContext, slug: str,
    created_by: UUID | None = None, campaign_id: UUID | None = None,
) -> tuple[UUID, str]:
    """Deterministically plan → validate → persist a page + its first (immutable) version."""
    brand = await resolve_brand(session, org_id)
    vertical = await org_vertical(session, org_id)
    strategy, spec = plan_page(campaign, brand, vertical)
    validate_spec(spec)  # raises SpecInvalid → 422 at the API

    await set_org_context(session, org_id)
    page_id = (
        await session.execute(
            text("INSERT INTO landing_pages (org_id, campaign_id, vertical, slug, status, "
                 "conversion_goal, created_by) "
                 "VALUES (:o,:c,:v,:s,'generated',:g,:by) RETURNING id"),
            {"o": str(org_id), "c": str(campaign_id) if campaign_id else None, "v": vertical,
             "s": slug, "g": spec.conversion_goal, "by": str(created_by) if created_by else None})
    ).scalar_one()
    version_id = (
        await session.execute(
            text("INSERT INTO landing_page_versions (page_id, org_id, version_no, "
                 "experience_strategy, spec, source_context, created_by) "
                 "VALUES (:p,:o,1,CAST(:es AS jsonb),CAST(:sp AS jsonb),CAST(:sc AS jsonb),:by) "
                 "RETURNING id"),
            {"p": str(page_id), "o": str(org_id), "es": json.dumps(strategy.to_dict()),
             "sp": json.dumps(spec.to_dict()),
             "sc": json.dumps({"headline": campaign.headline, "offer": campaign.offer,
                               "planner": "deterministic"}),
             "by": str(created_by) if created_by else None})
    ).scalar_one()
    await session.execute(
        text("UPDATE landing_pages SET current_version_id = :v WHERE id = :p"),
        {"v": str(version_id), "p": str(page_id)})
    return page_id, slug


async def current_spec(
    session: AsyncSession, org_id: UUID, page_id: UUID
) -> tuple[LandingPageSpec, UUID] | None:
    """The current version's validated spec (RLS-scoped to the caller's org)."""
    await set_org_context(session, org_id)
    row = (
        await session.execute(
            text("SELECT v.id, v.spec FROM landing_pages p "
                 "JOIN landing_page_versions v ON v.id = p.current_version_id WHERE p.id = :id"),
            {"id": str(page_id)})
    ).mappings().first()
    if row is None:
        return None
    spec = row["spec"]
    return LandingPageSpec.from_dict(json.loads(spec) if isinstance(spec, str) else spec), row["id"]


async def list_pages(session: AsyncSession, org_id: UUID) -> list[dict[str, Any]]:
    await set_org_context(session, org_id)
    rows = (
        await session.execute(
            text("SELECT id, slug, status, conversion_goal, created_at FROM landing_pages "
                 "ORDER BY created_at DESC"))
    ).mappings().all()
    return [dict(r) for r in rows]


async def record_public_event(
    session: AsyncSession, page_id: UUID, type_: str, *,
    item_ref: object = None, session_id: object = None, variant: object = None,
    utm: object = None, meta: object = None,
) -> bool:
    """Record a funnel event from the public track beacon. Tenant is resolved from `page_id` via the
    SECURITY-DEFINER `landing_page_org` (never from the request) — then the insert is RLS-scoped.
    The body is untrusted, so `item_ref`/`session_id`/`variant`/`utm`/`meta` are whitelisted +
    clamped (no PII, bounded sizes) before persistence."""
    if type_ not in TRACK_EVENT_TYPES:
        return False
    org = (
        await session.execute(
            text("SELECT landing_page_org(CAST(:p AS uuid))"), {"p": str(page_id)})
    ).scalar()
    if org is None:
        return False
    await set_org_context(session, org)
    version_id = (
        await session.execute(
            text("SELECT current_version_id FROM landing_pages WHERE id = :id"),
            {"id": str(page_id)})
    ).scalar()
    await session.execute(
        text("INSERT INTO landing_page_events "
             "(org_id, page_id, version_id, type, item_ref, variant, session_id, utm, meta) "
             "VALUES (:o,:p,:v,:t,:i,:var,:sid,CAST(:utm AS jsonb),CAST(:meta AS jsonb))"),
        {"o": str(org), "p": str(page_id), "v": str(version_id) if version_id else None,
         "t": type_, "i": _clip(item_ref, 64), "var": _clip(variant, 64) or "default",
         "sid": _clip(session_id, 64), "utm": json.dumps(_clip_utm(utm)),
         "meta": json.dumps(_clip_meta(meta))})
    return True


async def page_insights(
    session: AsyncSession, org_id: UUID, page_id: UUID
) -> dict[str, Any] | None:
    """"Which items are most wanted" + funnel counts for a page (RLS-scoped; None → 404)."""
    await set_org_context(session, org_id)
    exists = (
        await session.execute(
            text("SELECT 1 FROM landing_pages WHERE id = :id"), {"id": str(page_id)})
    ).scalar()
    if exists is None:
        return None
    counts = (
        await session.execute(
            text("SELECT type, count(*) AS n FROM landing_page_events "
                 "WHERE page_id = :p GROUP BY type"),
            {"p": str(page_id)})
    ).mappings().all()
    items = (
        await session.execute(
            text("SELECT item_ref, "
                 "count(*) FILTER (WHERE type = 'landing_page.item_clicked') AS clicks, "
                 "count(*) FILTER (WHERE type = 'landing_page.item_viewed') AS views "
                 "FROM landing_page_events "
                 "WHERE page_id = :p AND item_ref IS NOT NULL GROUP BY item_ref "
                 "ORDER BY clicks DESC, views DESC, item_ref LIMIT 20"),
            {"p": str(page_id)})
    ).mappings().all()
    events = {r["type"]: int(r["n"]) for r in counts}
    return {
        "page_id": str(page_id),
        "events": events,
        "total_events": sum(events.values()),
        "top_items": [
            {"item_ref": r["item_ref"], "clicks": int(r["clicks"]), "views": int(r["views"])}
            for r in items],
    }
