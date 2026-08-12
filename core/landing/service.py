"""Landing-page service (LP-1): create a page+version from a campaign context, resolve
brand/vertical, render, and record funnel events. Org-scoped → tenant context set before access.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.config import get_settings
from core.landing.plan import CampaignContext, plan_page
from core.landing.planner_llm import plan_variants_planned
from core.landing.spec import BrandTokens, ExperienceStrategy, LandingPageSpec
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


async def _insert_page(
    session: AsyncSession, org_id: UUID, *, vertical: str, slug: str, conversion_goal: str,
    created_by: UUID | None, campaign_id: UUID | None,
) -> UUID:
    return (
        await session.execute(
            text("INSERT INTO landing_pages (org_id, campaign_id, vertical, slug, status, "
                 "conversion_goal, created_by) "
                 "VALUES (:o,:c,:v,:s,'generated',:g,:by) RETURNING id"),
            {"o": str(org_id), "c": str(campaign_id) if campaign_id else None, "v": vertical,
             "s": slug, "g": conversion_goal, "by": str(created_by) if created_by else None})
    ).scalar_one()


async def _insert_version(
    session: AsyncSession, *, page_id: UUID, org_id: UUID, version_no: int,
    strategy: ExperienceStrategy, spec: LandingPageSpec, campaign: CampaignContext,
    variant_label: str, created_by: UUID | None, planner: str = "deterministic",
) -> UUID:
    provenance = {"headline": campaign.headline, "offer": campaign.offer,
                  "planner": planner, "variant": variant_label}
    if planner == "llm":  # record the model that made the semantic decisions (§18)
        provenance["model"] = get_settings().llm_model
    return (
        await session.execute(
            text("INSERT INTO landing_page_versions (page_id, org_id, version_no, "
                 "experience_strategy, spec, source_context, variant_label, created_by) "
                 "VALUES (:p,:o,:n,CAST(:es AS jsonb),CAST(:sp AS jsonb),CAST(:sc AS jsonb),"
                 ":vl,:by) RETURNING id"),
            {"p": str(page_id), "o": str(org_id), "n": version_no,
             "es": json.dumps(strategy.to_dict()), "sp": json.dumps(spec.to_dict()),
             "sc": json.dumps(provenance),
             "vl": variant_label, "by": str(created_by) if created_by else None})
    ).scalar_one()


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
    page_id = await _insert_page(
        session, org_id, vertical=vertical, slug=slug, conversion_goal=spec.conversion_goal,
        created_by=created_by, campaign_id=campaign_id)
    version_id = await _insert_version(
        session, page_id=page_id, org_id=org_id, version_no=1, strategy=strategy, spec=spec,
        campaign=campaign, variant_label="default", created_by=created_by)
    await session.execute(
        text("UPDATE landing_pages SET current_version_id = :v WHERE id = :p"),
        {"v": str(version_id), "p": str(page_id)})
    return page_id, slug


async def generate_variants(
    session: AsyncSession, org_id: UUID, *, campaign: CampaignContext, slug: str, n: int = 3,
    created_by: UUID | None = None, campaign_id: UUID | None = None, use_llm: bool = False,
) -> tuple[UUID, list[dict[str, Any]]]:
    """Generate N genuinely-different-UX candidates as immutable versions of one page.

    Variants come from the gated **LLM** strategy planner when `use_llm` + the provider is enabled
    (LP-2c), else the deterministic archetypes (LP-2a); either way each is validated + persisted and
    the planner used is recorded as provenance. The owner reviews + picks one (LP-2b). The page's
    `current_version_id` points at the first candidate so the page-level preview shows one."""
    brand = await resolve_brand(session, org_id)
    vertical = await org_vertical(session, org_id)
    planner, variants = await plan_variants_planned(
        campaign, brand, vertical, n=n, use_llm=use_llm)
    for _label, _strategy, spec in variants:
        validate_spec(spec)  # every candidate must be valid → 422 at the API otherwise

    await set_org_context(session, org_id)
    page_id = await _insert_page(
        session, org_id, vertical=vertical, slug=slug,
        conversion_goal=variants[0][2].conversion_goal, created_by=created_by,
        campaign_id=campaign_id)
    rows: list[dict[str, Any]] = []
    first_version_id: UUID | None = None
    for i, (label, strategy, spec) in enumerate(variants, start=1):
        version_id = await _insert_version(
            session, page_id=page_id, org_id=org_id, version_no=i, strategy=strategy, spec=spec,
            campaign=campaign, variant_label=label, created_by=created_by, planner=planner)
        first_version_id = first_version_id or version_id
        rows.append({"version_no": i, "variant_label": label})
    await session.execute(
        text("UPDATE landing_pages SET current_version_id = :v WHERE id = :p"),
        {"v": str(first_version_id), "p": str(page_id)})
    return page_id, rows


async def page_detail(
    session: AsyncSession, org_id: UUID, page_id: UUID
) -> dict[str, Any] | None:
    """Page status + the currently-selected variant (RLS-scoped; None → 404)."""
    await set_org_context(session, org_id)
    row = (
        await session.execute(
            text("SELECT p.id, p.slug, p.status, p.conversion_goal, p.created_at, "
                 "v.version_no AS current_version_no, v.variant_label AS current_variant_label "
                 "FROM landing_pages p "
                 "LEFT JOIN landing_page_versions v ON v.id = p.current_version_id "
                 "WHERE p.id = :id"),
            {"id": str(page_id)})
    ).mappings().first()
    return dict(row) if row else None


async def list_variants(
    session: AsyncSession, org_id: UUID, page_id: UUID
) -> list[dict[str, Any]] | None:
    """The page's candidate versions (RLS-scoped; None → 404). Newest-first version rows."""
    await set_org_context(session, org_id)
    if (await session.execute(
            text("SELECT 1 FROM landing_pages WHERE id = :id"), {"id": str(page_id)})).scalar() \
            is None:
        return None
    rows = (
        await session.execute(
            text("SELECT version_no, variant_label, created_at FROM landing_page_versions "
                 "WHERE page_id = :p ORDER BY version_no"),
            {"p": str(page_id)})
    ).mappings().all()
    return [dict(r) for r in rows]


async def version_spec(
    session: AsyncSession, org_id: UUID, page_id: UUID, version_no: int
) -> tuple[LandingPageSpec, str] | None:
    """A candidate version's validated spec + its variant label (RLS-scoped; None → 404)."""
    await set_org_context(session, org_id)
    row = (
        await session.execute(
            text("SELECT v.spec, v.variant_label FROM landing_page_versions v "
                 "JOIN landing_pages p ON p.id = v.page_id "
                 "WHERE v.page_id = :p AND v.version_no = :n"),
            {"p": str(page_id), "n": version_no})
    ).mappings().first()
    if row is None:
        return None
    spec = row["spec"]
    parsed = LandingPageSpec.from_dict(json.loads(spec) if isinstance(spec, str) else spec)
    return parsed, row["variant_label"]


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


async def published_spec(
    session: AsyncSession, page_id: UUID
) -> tuple[LandingPageSpec, str] | None:
    """The current version's spec for a **published** page, for public (unauth) serving (LP-3a).

    Tenant is resolved from `page_id` via the SECURITY-DEFINER `landing_page_org` (never a request
    value); the read is then RLS-scoped and gated on `status='published'` — so drafts, paused, and
    other tenants' pages all return `None` (→ 404), never leaked."""
    org = (
        await session.execute(
            text("SELECT landing_page_org(CAST(:p AS uuid))"), {"p": str(page_id)})
    ).scalar()
    if org is None:
        return None
    await set_org_context(session, org)
    row = (
        await session.execute(
            text("SELECT v.spec, v.variant_label FROM landing_pages p "
                 "JOIN landing_page_versions v ON v.id = p.current_version_id "
                 "WHERE p.id = :id AND p.status = 'published'"),
            {"id": str(page_id)})
    ).mappings().first()
    if row is None:
        return None  # unknown / not published / no current version → 404
    spec = row["spec"]
    parsed = LandingPageSpec.from_dict(json.loads(spec) if isinstance(spec, str) else spec)
    return parsed, row["variant_label"]


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
