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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.db import get_session
from core.landing import assets as landing_assets
from core.landing import leads, lifecycle, ratelimit, service
from core.landing.assets import AssetRejected
from core.landing.leads import LeadRejected
from core.landing.lifecycle import InvalidTransition
from core.landing.plan import (
    DEFAULT_VARIANTS,
    MAX_VARIANTS,
    CampaignContext,
    ProductRef,
)
from core.landing.render import render_html
from core.landing.validate import SpecInvalid
from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import CAMPAIGNS_READ, CAMPAIGNS_SEND
from core.tenancy.rbac import requires

router = APIRouter(prefix="/v1/landing", tags=["landing"])
public_router = APIRouter(tags=["landing-public"])  # LP-3a: unauth public serving

_TRACK_URL = "/v1/landing/track"  # where the rendered page's beacon posts

# In-app flood/bot defence caps (per IP, 60s window). The robust distributed limit is the reverse
# proxy at hosting time; these are the MVP in-process floor.
SERVE_PER_MIN = 120
TRACK_PER_MIN = 90
LEAD_PER_MIN = 10  # form submissions are rare + write PII → a much tighter cap

# Security headers for the served page. NOTE: no CSP header — the rendered HTML already carries a
# per-render nonce'd CSP in a <meta>; a header CSP (different/no nonce) would break the beacon.
_SECURITY_HEADERS = {
    "X-Robots-Tag": "noindex, nofollow",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cache-Control": "public, max-age=120",
}


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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
    # LP-2a/LP-4b: how many genuinely-different-UX candidates to generate for the owner to pick.
    # Default 3; a typical page does not need more, but up to MAX_VARIANTS is allowed.
    variants: int = Field(default=DEFAULT_VARIANTS, ge=1, le=MAX_VARIANTS)
    # LP-2c: use the gated LLM strategy planner for the variants (falls back to deterministic when
    # the provider is off — so this is a no-op unless a key is wired).
    use_llm: bool = False


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


class VersionSelect(BaseModel):
    version_no: int = Field(..., ge=1)


class LandingStatus(BaseModel):
    page_id: UUID
    status: str


class LandingPageDetail(BaseModel):
    id: UUID
    slug: str
    status: str
    conversion_goal: str
    current_version_no: int | None = None
    current_variant_label: str | None = None
    created_at: datetime


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
                created_by=current.user_id, campaign_id=body.campaign_id, use_llm=body.use_llm)
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
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> None:
    # Best-effort + fail-closed: unknown page or disallowed type simply records nothing (still 204,
    # so the beacon never leaks whether a page exists). The untrusted body is whitelisted + clamped
    # in the service. LP-3a: a per-IP flood is silently dropped (still 204 — a 429 leaks a signal).
    if not ratelimit.allow(f"track:{_client_ip(request)}", TRACK_PER_MIN):
        return
    await service.record_public_event(
        session, body.page_id, body.type, item_ref=body.item_ref, session_id=body.session_id,
        variant=body.variant, utm=body.utm, meta=body.meta)


@public_router.get("/p/{page_id}", response_class=HTMLResponse,
                   summary="Public: serve a published landing page (no auth)")
async def serve_public_page(
    page_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    # Public, unauthenticated. A per-IP flood → 429; only PUBLISHED pages are ever served (drafts,
    # paused, other tenants → 404 via the SECDEF + status gate — no leak). Live public serving is
    # hosting-gated (DNS/reverse proxy); the route itself is complete + tested here.
    if not ratelimit.allow(f"serve:{_client_ip(request)}", SERVE_PER_MIN):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limited")
    result = await service.published_spec(session, page_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    spec, variant = result
    html = render_html(spec, page_id=str(page_id), track_url=_TRACK_URL, variant=variant)
    return HTMLResponse(html, headers=_SECURITY_HEADERS)


class LeadIn(BaseModel):
    """The public lead form. PII — validated + capped here, never logged."""
    phone: str = Field(..., min_length=6, max_length=32)
    name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=200)
    consent: bool = False
    item_ref: str | None = Field(default=None, max_length=64)
    session_id: str | None = Field(default=None, max_length=64)
    utm: dict[str, str] = Field(default_factory=dict)


class LeadAccepted(BaseModel):
    ok: bool = True


@public_router.post("/p/{page_id}/lead", response_model=LeadAccepted,
                    status_code=status.HTTP_202_ACCEPTED,
                    summary="Public: submit the lead form on a published page (no auth)")
async def submit_lead(
    page_id: UUID,
    body: LeadIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> LeadAccepted:
    # Public + unauthenticated + PII. Consent is required (422 without it — that's about the body,
    # not the page). An unknown/unpublished page records nothing but still answers neutrally, so the
    # endpoint never reveals whether a page exists.
    if not ratelimit.allow(f"lead:{_client_ip(request)}", LEAD_PER_MIN):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limited")
    try:
        await leads.capture_lead(
            session, page_id, phone=body.phone, name=body.name, email=body.email,
            consent=body.consent, item_ref=body.item_ref, utm=body.utm,
            session_id=body.session_id)
    except LeadRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return LeadAccepted()


# --- Lifecycle + owner approval (LP-2b) -----------------------------------------------------------

@router.get("/pages/{page_id}", response_model=LandingPageDetail,
            summary="Page status + the currently-selected variant (owner)")
async def get_page(
    page_id: UUID,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_READ)),
    session: AsyncSession = Depends(get_db),
) -> LandingPageDetail:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    data = await service.page_detail(session, current.org_id, page_id)
    if data is None:  # RLS-scoped → unknown/other-org both 404
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page not found")
    return LandingPageDetail(**data)


def _status_or_404(page_id: UUID, new_status: str | None) -> LandingStatus:
    if new_status is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page not found")
    return LandingStatus(page_id=page_id, status=new_status)


@router.post("/pages/{page_id}/select", response_model=LandingStatus,
             summary="Owner approves + selects one candidate variant — HITL gate #1 (owner)")
async def select_page_variant(
    page_id: UUID,
    body: VersionSelect,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_SEND)),
    session: AsyncSession = Depends(get_db),
) -> LandingStatus:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        new_status = await lifecycle.select_variant(
            session, current.org_id, page_id, body.version_no, current.user_id)
    except InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _status_or_404(page_id, new_status)  # None → page/version not found


@router.post("/pages/{page_id}/submit", response_model=LandingStatus,
             summary="Submit a page for approval (owner)")
async def submit_page(
    page_id: UUID,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_SEND)),
    session: AsyncSession = Depends(get_db),
) -> LandingStatus:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        new_status = await lifecycle.submit_for_approval(
            session, current.org_id, page_id, current.user_id)
    except InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _status_or_404(page_id, new_status)


@router.post("/pages/{page_id}/publish", response_model=LandingStatus,
             summary="Publish an approved page — mark + record (live serving is LP-3a) (owner)")
async def publish_page(
    page_id: UUID,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_SEND)),
    session: AsyncSession = Depends(get_db),
) -> LandingStatus:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        new_status = await lifecycle.publish(session, current.org_id, page_id, current.user_id)
    except InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _status_or_404(page_id, new_status)


@router.post("/pages/{page_id}/pause", response_model=LandingStatus,
             summary="Pause a published page (owner)")
async def pause_page(
    page_id: UUID,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_SEND)),
    session: AsyncSession = Depends(get_db),
) -> LandingStatus:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        new_status = await lifecycle.pause(session, current.org_id, page_id, current.user_id)
    except InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _status_or_404(page_id, new_status)


@router.post("/pages/{page_id}/rollback", response_model=LandingStatus,
             summary="Roll the current version back to an earlier candidate → approved (owner)")
async def rollback_page(
    page_id: UUID,
    body: VersionSelect,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_SEND)),
    session: AsyncSession = Depends(get_db),
) -> LandingStatus:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        new_status = await lifecycle.rollback(
            session, current.org_id, page_id, body.version_no, current.user_id)
    except InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _status_or_404(page_id, new_status)


@router.post("/pages/{page_id}/archive", response_model=LandingStatus,
             summary="Archive a page (owner)")
async def archive_page(
    page_id: UUID,
    current: CurrentAuth = Depends(requires(CAMPAIGNS_SEND)),
    session: AsyncSession = Depends(get_db),
) -> LandingStatus:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        new_status = await lifecycle.archive(session, current.org_id, page_id, current.user_id)
    except InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _status_or_404(page_id, new_status)


@router.post("/pages/from-upload", response_model=LandingCreated,
             status_code=status.HTTP_201_CREATED,
             summary="Upload campaign photos → auto-generate candidate pages (owner)")
async def create_from_upload(
    slug: str = Form(..., min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9\-]*$"),
    headline: str = Form(..., min_length=1, max_length=200),
    offer: str = Form(default="", max_length=200),
    subheadline: str = Form(default="", max_length=300),
    objective: str = Form(default="whatsapp"),
    wa_number: str = Form(default="", max_length=32),
    product_titles: str = Form(default=""),   # newline- or comma-separated, in image order
    variants: int = Form(default=DEFAULT_VARIANTS, ge=1, le=MAX_VARIANTS),
    files: list[UploadFile] = File(...),
    current: CurrentAuth = Depends(requires(CAMPAIGNS_SEND)),
    session: AsyncSession = Depends(get_db),
) -> LandingCreated:
    """The owner's dashboard trigger: photos in, candidate pages out (they still pick + publish).

    Every byte is MIME-checked, size-capped and **AV-scanned fail-closed** before it is stored."""
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    payloads: list[tuple[str, str, bytes]] = []
    for f in files:
        payloads.append((f.filename or "image", f.content_type or "", await f.read()))
    titles = [t.strip() for t in product_titles.replace(",", "\n").split("\n") if t.strip()]
    try:
        stored = await landing_assets.store_assets(current.org_id, payloads)
        campaign = landing_assets.build_campaign(
            headline=headline, offer=offer, subheadline=subheadline, objective=objective,
            wa_number=wa_number, assets=stored, product_titles=titles)
        page_id, rows = await landing_assets.generate_from_upload(
            session, current.org_id, campaign=campaign, slug=slug, n=variants,
            created_by=current.user_id)
    except AssetRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except SpecInvalid as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"invalid page: {exc}") from exc
    return LandingCreated(
        page_id=page_id, slug=slug, preview_url=f"/v1/landing/pages/{page_id}/preview",
        variants=_variant_rows(page_id, rows))
