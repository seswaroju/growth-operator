"""Plan entitlements — which capabilities a store's plan actually includes (ENT-1a).

Before this, only **seats** (CP-3) and **agents** (CP-2b) were plan-gated: a starter-tier store
could still open Campaigns and send one, because the only check was a *role* permission.
Tiering is a revenue boundary, so it is enforced **server-side** here; hiding things in the UI
(ENT-1b) is convenience, never the boundary.

Two rules keep this safe to introduce mid-flight:

1. **A baseline every plan includes** — the things no tier would ever sell separately. Existing
   stores keep working unchanged (today every plan's `features` list is empty).
2. **Additive per plan** — a tier grants extra features on top of the baseline, stored in the
   existing `billing_plans.features` column (written by the plan builder since CP-1, never read
   until now — so **no migration**).

Generic/platform-invariant: these are platform capabilities, never vertical nouns (Rule Zero).
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.deps import CurrentAuth, get_current_auth
from core.tenancy.middleware import get_db
from core.tenancy.repository import set_org_context

# ---- The catalog ------------------------------------------------------------------------------
# Baseline: included in every plan, including the entry tier.
CONVERSATIONS = "conversations"        # the inbox + concierge replies
CATALOG = "catalog"                    # catalog + pricing/quotes
CUSTOMERS = "customers"                # CRM: contacts, leads, timeline
GHOST_RECOVERY = "ghost_recovery"      # silent-lead diagnosis + recovery (the wedge)

# Tier-differentiated: a plan must grant these explicitly.
CAMPAIGNS_WHATSAPP = "campaigns.whatsapp"   # bulk WhatsApp campaigns
LANDING_PAGES = "landing_pages"             # generated landing pages for paid traffic
ADS_INSTAGRAM = "ads.instagram"             # Instagram publishing / ads
ADS_GOOGLE = "ads.google"                   # Google Ads campaigns
SEO = "seo"                                 # SEO/content surface (not built yet — grantable early)
AGENT_MARKETING = "agent.marketing"         # a dedicated marketing agent for the store

BASELINE_FEATURES: frozenset[str] = frozenset(
    {CONVERSATIONS, CATALOG, CUSTOMERS, GHOST_RECOVERY})

GRANTABLE_FEATURES: tuple[str, ...] = (
    CAMPAIGNS_WHATSAPP, LANDING_PAGES, ADS_INSTAGRAM, ADS_GOOGLE, SEO, AGENT_MARKETING,
)

ALL_FEATURES: frozenset[str] = BASELINE_FEATURES | frozenset(GRANTABLE_FEATURES)

FEATURE_LABEL: dict[str, str] = {
    CONVERSATIONS: "Conversations",
    CATALOG: "Catalog & quotes",
    CUSTOMERS: "Customers",
    GHOST_RECOVERY: "Silent-lead recovery",
    CAMPAIGNS_WHATSAPP: "WhatsApp campaigns",
    LANDING_PAGES: "Landing pages",
    ADS_INSTAGRAM: "Instagram ads",
    ADS_GOOGLE: "Google Ads",
    SEO: "SEO",
    AGENT_MARKETING: "Dedicated marketing agent",
}


class FeatureNotInPlan(Exception):
    """Raised by `requires_feature(f)` when the store's plan does not include `f` (→ 403)."""

    def __init__(self, feature: str):
        self.feature = feature
        label = FEATURE_LABEL.get(feature, feature)
        super().__init__(f"{label} is not included in this plan")


def normalize(raw: object) -> frozenset[str]:
    """Only recognised feature ids survive — a typo in a plan never grants something real."""
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(f for f in raw if isinstance(f, str) and f in ALL_FEATURES)


async def entitlements(session: AsyncSession, org_id: UUID) -> frozenset[str]:
    """The store's capabilities: the baseline plus whatever its **active** plan grants.

    A store with no active subscription still gets the baseline — the entry experience — rather than
    being locked out of its own inbox."""
    await set_org_context(session, org_id)
    row = (
        await session.execute(
            text("SELECT p.features FROM billing_subscriptions s "
                 "JOIN billing_plans p ON p.id = s.plan_id "
                 "WHERE s.org_id = :o AND s.status = 'active' "
                 "ORDER BY s.started_at DESC LIMIT 1"),
            {"o": str(org_id)})
    ).mappings().first()
    granted: frozenset[str] = frozenset()
    if row is not None:
        raw = row["features"]
        if isinstance(raw, str):
            import json
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = []
        granted = normalize(raw)
    return BASELINE_FEATURES | granted


async def has_feature(session: AsyncSession, org_id: UUID, feature: str) -> bool:
    return feature in await entitlements(session, org_id)


def requires_feature(feature: str) -> Callable[..., object]:
    """Dependency factory mirroring `requires(permission)`: admit only if the plan includes
    `feature`. Role permissions and plan entitlements are **independent** gates — a caller needs
    both."""

    async def _dependency(
        current: CurrentAuth = Depends(get_current_auth),
        session: AsyncSession = Depends(get_db),
    ) -> CurrentAuth:
        if current.org_id is None:
            raise FeatureNotInPlan(feature)
        if not await has_feature(session, current.org_id, feature):
            raise FeatureNotInPlan(feature)
        return current

    return _dependency


async def feature_not_in_plan_handler(request: object, exc: Exception) -> object:
    """RFC7807 for a plan miss — mirrors the RBAC handler so clients get a consistent shape."""
    from fastapi.responses import JSONResponse

    assert isinstance(exc, FeatureNotInPlan)
    return JSONResponse(
        status_code=403,
        media_type="application/problem+json",
        content={
            "type": "https://growthoperator.dev/errors/feature_not_in_plan",
            "title": "Not included in this plan",
            "status": 403,
            "detail": str(exc),
            "feature": exc.feature,
        },
    )


def register_entitlement_handlers(app: object) -> None:
    app.add_exception_handler(FeatureNotInPlan, feature_not_in_plan_handler)  # type: ignore[attr-defined]
