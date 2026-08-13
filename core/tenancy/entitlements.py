"""Tenant effective-access resolver — what a store's plan actually lets it use (ENT-1a, PLAN-1).

Before ENT-1a, only **seats** (CP-3) and **agents** (CP-2b) were plan-gated: a starter-tier store
could still open Campaigns and send one, because the only check was a *role* permission.
Tiering is a revenue boundary, so it is enforced **server-side** here; hiding things in the UI
is convenience, never the boundary.

**Division of labour (PLAN-1).** `core/tenancy/capabilities.py` owns the canonical, global,
org-independent product vocabulary — what capabilities exist. This module owns the tenant-specific
question — what *this* store may use. The catalog is deliberately wider: a capability may be
declared `runtime_grantable` (eligible to become an independent machine entitlement once PLAN-2's
structured resolver lands) long before it is effective for anyone.

**PLAN-1 changes vocabulary, not authorization.** The effective set stays frozen at
`LEGACY_EFFECTIVE_KEYS` — the exact ENT-1a set minus four keys that were grantable but unsafe
(`seo` and `agent.marketing` are not built; `ads.instagram` and `ads.google` have no
customer-reachable path). Nothing newly declared in the catalog becomes effective here. PLAN-2 is
where the resolver intentionally adopts the structured contract, adds provenance, resolves
no-active-subscription semantics, and filters vertical capabilities against a tenant's installed
packs.

Generic/platform-invariant: these are platform capabilities, never vertical nouns (Rule Zero).
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.capabilities import L0_CAPABILITIES, resolve_alias
from core.tenancy.deps import CurrentAuth, get_current_auth
from core.tenancy.middleware import get_db
from core.tenancy.repository import set_org_context

# ---- Keys (canonical spellings live in capabilities.py) ---------------------------------------
CONVERSATIONS = "conversations"        # the inbox + concierge replies
CATALOG = "catalog"                    # catalog + pricing/quotes
CUSTOMERS = "customers"                # CRM: contacts, leads, timeline
GHOST_RECOVERY = "ghost_recovery"      # silent-lead diagnosis + recovery (the wedge)
CAMPAIGNS_WHATSAPP = "campaigns.whatsapp"   # bulk WhatsApp campaigns
LANDING_PAGES = "landing_pages"             # generated landing pages for paid traffic

# Retained so historical imports and stored plan rows keep resolving. None of these grant anything:
# `resolve_alias` maps them onto their canonical capability, which is then refused below.
ADS_INSTAGRAM = "ads.instagram"        # → social.instagram_publishing (partial, internal)
ADS_GOOGLE = "ads.google"              # partial, internal
SEO = "seo"                            # planned — not built
AGENT_MARKETING = "agent.marketing"    # planned — no such archetype

# ---- The PLAN-1 compatibility shim -------------------------------------------------------------
# **This is not the catalog.** It is the frozen ENT-1a effective vocabulary minus the four unsafe
# keys, and it is the ONLY thing `normalize()` consults. A capability cannot become effective by
# being added to the catalog — that requires deliberately editing this set, which a unit test
# guards. PLAN-2 replaces the shim with the structured resolver.
LEGACY_EFFECTIVE_KEYS: frozenset[str] = frozenset({
    CONVERSATIONS, CATALOG, CUSTOMERS, GHOST_RECOVERY,   # ENT-1a baseline
    CAMPAIGNS_WHATSAPP, LANDING_PAGES,                   # ENT-1a grantable, retained
})

# Compatibility shim, **not** a product tier. A store with no active subscription retaining baseline
# access is a migration accommodation so existing stores are not locked out of their own inbox
# mid-flight. This is not "free Recover". PLAN-2 defines the final no-active-subscription semantics.
BASELINE_FEATURES: frozenset[str] = frozenset(
    {CONVERSATIONS, CATALOG, CUSTOMERS, GHOST_RECOVERY})

# What a plan may additionally grant today.
GRANTABLE_FEATURES: tuple[str, ...] = (CAMPAIGNS_WHATSAPP, LANDING_PAGES)

ALL_FEATURES: frozenset[str] = BASELINE_FEATURES | frozenset(GRANTABLE_FEATURES)

# Owner-facing names come from the canonical catalog — one source of truth for copy. L0 only, so
# importing this module never touches the filesystem.
FEATURE_LABEL: dict[str, str] = {c.key: c.label for c in L0_CAPABILITIES}


class FeatureNotInPlan(Exception):
    """Raised by `requires_feature(f)` when the store's plan does not include `f` (→ 403)."""

    def __init__(self, feature: str):
        self.feature = feature
        label = FEATURE_LABEL.get(feature, feature)
        super().__init__(f"{label} is not included in this plan")


def normalize(raw: object) -> frozenset[str]:
    """Reduce a stored plan `features` list to what it may actually grant today.

    A key survives only if it is in `LEGACY_EFFECTIVE_KEYS`. Historical spellings are mapped
    through `resolve_alias` first, so an old row is understood rather than silently ignored — and
    then refused on its merits if its capability is not effective. A typo, an unknown key, a
    not-built capability (`seo`), an unreachable one (`ads.google`) and any **pack-contributed**
    capability all grant nothing. Pack-contributed keys are refused because activating one safely
    requires knowing which packs the tenant installed, and this function has no such context —
    PLAN-2's resolver owns that."""
    if not isinstance(raw, list):
        return frozenset()
    granted: set[str] = set()
    for f in raw:
        if not isinstance(f, str):
            continue
        canonical = resolve_alias(f)
        if canonical in LEGACY_EFFECTIVE_KEYS:
            granted.add(canonical)
    return frozenset(granted)


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
