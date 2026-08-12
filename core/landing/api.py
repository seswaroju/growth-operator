"""Landing-page API (LP-1).

Owner routes (campaign perms) create a page from a campaign context and **preview** the rendered
tenant-branded page; a **public** track beacon records CTA/view events (tenant resolved from page_id
via the SECURITY-DEFINER lookup, never a payload). No public production serving / domains /
experiments here — LP-1 is a gated demo of the full slice.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.db import get_session
from core.landing import service
from core.landing.plan import CampaignContext, ProductRef
from core.landing.render import render_html
from core.landing.validate import SpecInvalid
from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import CAMPAIGNS_READ, CAMPAIGNS_SEND
from core.tenancy.rbac import requires

router = APIRouter(prefix="/v1/landing", tags=["landing"])

_TRACK_URL = "/v1/landing/track"  # where the rendered page's beacon posts


class ProductIn(BaseModel):
    title: str
    price_text: str = ""
    image_url: str | None = None


class LandingCreate(BaseModel):
    slug: str = Field(..., min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9\-]*$")
    headline: str = Field(..., min_length=1, max_length=200)
    offer: str = Field(default="", max_length=200)
    subheadline: str = Field(default="", max_length=300)
    objective: str = Field(default="whatsapp")
    hero_image_url: str | None = None
    products: list[ProductIn] = Field(default_factory=list)
    campaign_id: UUID | None = None
    # LP-2a: >1 generates that many genuinely-different-UX candidate versions for the owner to pick.
    variants: int = Field(default=1, ge=1, le=3)


class VariantRow(BaseModel):
    version_no: int
    variant_label: str
    preview_url: str


class LandingCreated(BaseModel):
    page_id: UUID
    slug: str
    preview_url: str
    variants: list[VariantRow] = Field(default_factory=list)


class LandingPageRow(BaseModel):
    id: UUID
    slug: str
    status: str
    conversion_goal: str
    created_at: datetime


class TrackIn(BaseModel):
    # A public, fire-and-forget beacon: accept loosely and let the service whitelist + clamp every
    # field, so a malformed/hostile body still records best-effort (or nothing) — never a 422 that
    # would tell a probe its shape was wrong. Body-size limiting / rate-limiting is LP-3.
    page_id: UUID
    type: str
    item_ref: str | None = None
    session_id: str | None = None
    variant: str | None = None
    utm: dict[str, str] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class ItemInterest(BaseModel):
    item_ref: str
    clicks: int
    views: int


class PageInsights(BaseModel):
    page_id: UUID
    events: dict[str, int]
    total_events: int
    top_items: list[ItemInterest]


@router.post("/pages", response_model=LandingCreated, status_code=status.HTTP_201_CREATED,
             summary="Generate a campaign landing page (deterministic; owner)")
async def create_page(
    body: LandingCreate,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_SEND)),
    session: AsyncSession = Depends(get_db),
) -> LandingCreated:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    campaign = CampaignContext(
        headline=body.headline, offer=body.offer, subheadline=body.subheadline,
        objective=body.objective, hero_image_url=body.hero_image_url,
        products=[ProductRef(p.title, p.price_text, p.image_url) for p in body.products])
    try:
        if body.variants > 1:
            page_id, rows = await service.generate_variants(
                session, current.org_id, campaign=campaign, slug=body.slug, n=body.variants,
                created_by=current.user_id, campaign_id=body.campaign_id)
        else:
            page_id, _slug = await service.create_landing_page(
                session, current.org_id, campaign=campaign, slug=body.slug,
                created_by=current.user_id, campaign_id=body.campaign_id)
            rows = [{"version_no": 1, "variant_label": "default"}]
    except SpecInvalid as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"invalid page: {exc}") from exc
    return LandingCreated(
        page_id=page_id, slug=body.slug, preview_url=f"/v1/landing/pages/{page_id}/preview",
        variants=_variant_rows(page_id, rows))


def _variant_rows(page_id: UUID, rows: list[dict[str, Any]]) -> list[VariantRow]:
    return [
        VariantRow(version_no=r["version_no"], variant_label=r["variant_label"],
                   preview_url=f"/v1/landing/pages/{page_id}/versions/{r['version_no']}/preview")
        for r in rows]


@router.get("/pages", response_model=list[LandingPageRow],
            summary="List this store's pages (owner)")
async def list_pages(
    current: CurrentAuth = Depends(requires(CAMPAIGNS_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[LandingPageRow]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    return [LandingPageRow(**r) for r in await service.list_pages(session, current.org_id)]


@router.get("/pages/{page_id}/preview", response_class=HTMLResponse,
            summary="Preview the rendered tenant-branded page (owner)")
async def preview_page(
    page_id: UUID,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_READ)),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    result = await service.current_spec(session, current.org_id, page_id)
    if result is None:  # RLS scopes to the caller's org → unknown/other-org both 404
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page not found")
    spec, _version_id = result
    return HTMLResponse(render_html(spec, page_id=str(page_id), track_url=_TRACK_URL))


@router.get("/pages/{page_id}/variants", response_model=list[VariantRow],
            summary="List the page's candidate variants for the owner to choose from (owner)")
async def list_variants(
    page_id: UUID,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[VariantRow]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    rows = await service.list_variants(session, current.org_id, page_id)
    if rows is None:  # RLS-scoped → unknown/other-org both 404
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page not found")
    return _variant_rows(page_id, rows)


@router.get("/pages/{page_id}/versions/{version_no}/preview", response_class=HTMLResponse,
            summary="Preview a specific candidate variant (owner)")
async def preview_version(
    page_id: UUID,
    version_no: int,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_READ)),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    result = await service.version_spec(session, current.org_id, page_id, version_no)
    if result is None:  # RLS-scoped → unknown/other-org both 404
        raise HTTPException(status.HTTP_404_NOT_FOUND, "variant not found")
    spec, variant_label = result
    return HTMLResponse(render_html(
        spec, page_id=str(page_id), track_url=_TRACK_URL, variant=variant_label))


@router.get("/pages/{page_id}/insights", response_model=PageInsights,
            summary="Which items are most wanted + funnel counts for a page (owner)")
async def get_insights(
    page_id: UUID,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_READ)),
    session: AsyncSession = Depends(get_db),
) -> PageInsights:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    data = await service.page_insights(session, current.org_id, page_id)
    if data is None:  # RLS-scoped → unknown/other-org both 404
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page not found")
    return PageInsights(**data)


@router.post("/track", status_code=status.HTTP_204_NO_CONTENT,
             summary="Public funnel beacon (view / item / CTA) — tenant resolved from page_id")
async def track(
    body: TrackIn,
    session: AsyncSession = Depends(get_session),
) -> None:
    # Best-effort + fail-closed: unknown page or disallowed type simply records nothing (still 204,
    # so the beacon never leaks whether a page exists). The untrusted body is whitelisted + clamped
    # in the service. LP-3 adds rate-limiting + bot defence.
    await service.record_public_event(
        session, body.page_id, body.type, item_ref=body.item_ref, session_id=body.session_id,
        variant=body.variant, utm=body.utm, meta=body.meta)
