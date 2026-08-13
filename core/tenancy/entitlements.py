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

**PLAN-2 adopts the structured contract.** `resolve()` returns one structured commercial read
model — capabilities, agents, channels, limits, provenance and exclusions — from
`billing_plans.config` (see `plan_config.py`). Machine authorization now lives in
`config.entitlements`; the free-text `billing_plans.features` column is **legacy compatibility
input only**, never permanent authority.

**No active subscription means zero paid capabilities.** ENT-1a returned a baseline to any store,
subscribed or not; that was a migration accommodation, not a product tier, and it is gone. This is
not "free Recover" — authentication and RBAC, not entitlements, govern account and data access, and
none of the account/support/export/privacy routes are entitlement-gated. An **active legacy** plan
still reconstructs ENT-1a's historical semantics inside the compatibility loader, so no existing
subscriber silently loses what it had.

**PLAN-2 resolves; it does not enforce.** The four existing `requires_feature` gates are unchanged
and nothing new became plan-gated: `catalog.ingestion`, `campaigns.analytics` and pack capabilities
are computed here but still ungated on their routes. PLAN-5 owns enforcement expansion, including
reconciling agent instances when a plan is reassigned.

Generic/platform-invariant: these are platform capabilities, never vertical nouns (Rule Zero).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.channels.registry import CHANNEL_TYPES
from core.tenancy.capabilities import L0_CAPABILITIES, by_key, resolve_alias
from core.tenancy.deps import CurrentAuth, get_current_auth
from core.tenancy.middleware import get_db
from core.tenancy.plan_config import PlanConfig, parse_plan_config, parse_promotions
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

# Owner-facing names come from the canonical catalog — one source of truth for copy. L0 only, so
# importing this module never touches the filesystem.
FEATURE_LABEL: dict[str, str] = {c.key: c.label for c in L0_CAPABILITIES}


class FeatureNotInPlan(Exception):
    """Raised by `requires_feature(f)` when the store's plan does not include `f` (→ 403)."""

    def __init__(self, feature: str):
        self.feature = feature
        label = FEATURE_LABEL.get(feature, feature)
        super().__init__(f"{label} is not included in this plan")


# ---- Structured effective entitlements (PLAN-2) ------------------------------------------------


@dataclass(frozen=True)
class Grant:
    """Why one capability is effective. `legacy_compat` is never reported as a native plan grant —
    PLAN-4 uses that distinction to find plans still needing migration to the structured schema."""

    key: str
    source: Literal["plan", "promotion", "legacy_compat"]
    promotion_label: str | None = None
    ends_at: datetime | None = None  # promotions only; tz-aware UTC


@dataclass(frozen=True)
class Excluded:
    """Something the plan asked for that did not become effective, and why. Kept so an operator can
    be told *"you typed `seo`; it granted nothing because it is planned"* rather than it silently
    vanishing."""

    key: str
    component: Literal["capability", "agent", "channel", "promotion", "config"]
    reason: str


@dataclass(frozen=True)
class EffectiveLimits:
    """Reported for preview/debugging. **CP-3 remains the only seat enforcement** — nothing here
    creates a second mechanism."""

    max_managers: int = 0
    max_staff: int = 0


@dataclass(frozen=True)
class EffectiveEntitlements:
    """One structured commercial read model for a tenant.

    `capabilities` is the **only** component consumed by `requires_feature()`. `agents` and
    `channels` are separate structured components deliberately *not* folded into capability keys:
    agents stay enforced by CP-2b, and a channel selection is a commercial choice that says nothing
    about whether the channel is connected, provisioned, live, or consented — that is operational
    readiness, resolved elsewhere."""

    capabilities: frozenset[str] = frozenset()
    agents: frozenset[str] = frozenset()
    channels: frozenset[str] = frozenset()
    limits: EffectiveLimits = EffectiveLimits()
    grants: tuple[Grant, ...] = ()
    excluded: tuple[Excluded, ...] = ()
    subscription_state: Literal["active", "cancelled", "none"] = "none"
    plan_id: UUID | None = None
    plan_name: str | None = None
    addons: tuple[str, ...] = ()  # display metadata only — never authorization

    def __contains__(self, key: str) -> bool:
        return key in self.capabilities


# ENT-1a's historical paid baseline. It exists **only** inside the legacy compatibility loader and
# is not a product tier: a legacy plan predates structured entitlements, so without reconstructing
# what ENT-1a implicitly granted, a legacy `campaigns.whatsapp` plan would lose `customers` and the
# dependency validator would correctly — but destructively — reject it. Deliberately private.
_LEGACY_ENT1A_BASELINE: frozenset[str] = frozenset(
    {CONVERSATIONS, CATALOG, CUSTOMERS, GHOST_RECOVERY})

_SUBSCRIPTION_SQL = text(
    # One row always. `ever.existed` distinguishes "cancelled history" from "never subscribed"
    # deterministically in the query rather than inferring it from unrelated state.
    # `billing_plans.active` is deliberately NOT filtered: that flag means "eligible for new
    # assignment", so retiring a plan must not revoke an existing active subscriber's access.
    "WITH act AS (SELECT plan_id FROM billing_subscriptions "
    "             WHERE org_id = :o AND status = 'active'), "
    "     ever AS (SELECT EXISTS (SELECT 1 FROM billing_subscriptions WHERE org_id = :o) "
    "                     AS existed) "
    "SELECT ever.existed, act.plan_id, p.name AS plan_name, p.features, p.config, "
    "       p.max_managers, p.max_staff "
    "FROM ever LEFT JOIN act ON true LEFT JOIN billing_plans p ON p.id = act.plan_id"
)

_TENANT_AGENTS_SQL = text(
    # An archetype the tenant is actually *bound* to. `agent_instances.status` is deliberately not
    # filtered: paused / shadow / circuit_open are operational states, not entitlement truth. This
    # is inherently pack-aware because a binding only exists via the pack that created it.
    "SELECT DISTINCT ar.slug FROM agent_instances ai "
    "JOIN agent_bindings ab ON ab.id = ai.binding_id "
    "JOIN agent_archetypes ar ON ar.id = ab.archetype_id "
    "WHERE ai.org_id = :o"
)

_INSTALLED_PACKS_SQL = text(
    "SELECT p.slug FROM pack_installations pi JOIN packs p ON p.id = pi.pack_id "
    "WHERE pi.org_id = :o AND pi.status = 'active'"
)


def normalize(raw: object) -> frozenset[str]:
    """Reduce a stored legacy `features` list to the capabilities it may actually grant.

    Historical spellings are mapped through `resolve_alias` first, so an old row is understood
    rather than silently ignored — and then refused on its merits. A typo, an unknown key, a
    not-built capability (`seo`), an unreachable one (`ads.google`) and any pack-contributed
    capability all grant nothing here; pack-contributed keys need installed-pack context, which
    this context-free function does not have (the resolver applies it)."""
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


def implied_legacy_channels(capabilities: frozenset[str]) -> frozenset[str]:
    """Channels a set of **legacy** capabilities necessarily requires.

    Legacy plans predate `config.channels`, so a legacy `campaigns.whatsapp` grant carries no
    channel selection — and under component-aware dependency validation it would fail closed,
    silently breaking a plan that worked under ENT-1a. Derived from the canonical catalog's own
    `depends_on` metadata rather than a hardcoded list, so it cannot drift from PLAN-1.

    **Legacy compatibility only.** An implied channel is *not* evidence that the plan explicitly
    chose that channel; it is reconstructed state, and the resolver records it as such so a future
    plan builder cannot mistake it for a historical operator decision. Nothing else is implied —
    no capabilities, agents, addons, limits or arbitrary dependencies."""
    out: set[str] = set()
    for key in capabilities:
        cap = by_key(key)
        if cap is None:
            continue
        for dep in cap.depends_on:
            dep_cap = by_key(dep)
            if dep_cap is not None and dep_cap.kind == "channel":
                out.add(dep.removeprefix("channel."))
    return frozenset(out)


def dependency_satisfied(
    dep: str, capabilities: set[str], channels: set[str], agents: set[str]
) -> tuple[bool, str]:
    """Component-aware dependency check.

    PLAN-1 deliberately contains dependencies that live outside `capabilities` —
    `campaigns.whatsapp` needs `channel.whatsapp` (a channel, not runtime-grantable) and a pack
    capability may need `pricing` (governed by RBAC). Requiring every dependency to be a granted
    capability would wrongly drop both. Satisfaction is therefore decided by the dependency's
    canonical kind/governance, and a missing dependency is **never** auto-granted."""
    cap = by_key(dep)
    if cap is None:
        return False, f"unknown_dependency:{dep}"
    if cap.kind == "channel":
        return (
            (dep.removeprefix("channel.") in channels),
            f"missing_channel_selection:{dep}",
        )
    if cap.kind == "agent":
        return (dep.removeprefix("agent.") in agents), f"missing_agent_selection:{dep}"
    if cap.kind == "limit":
        return True, ""  # CP-3 owns limits
    if cap.runtime_grantable:
        return (dep in capabilities), f"missing_dependency:{dep}"
    if cap.enforced_by and cap.enforced_by.startswith("rbac:"):
        # Governed per-request per-user by role permissions, not per-plan: structurally satisfied.
        return True, ""
    return False, f"governed_elsewhere:{cap.enforced_by or 'unknown'}"


@dataclass(frozen=True)
class ResolutionContext:
    """Everything about a *store* that entitlement composition needs, gathered once.

    Separating this out is what lets the operator plan builder preview a plan that no tenant is on
    without inventing a fake tenant or a throwaway subscription: it supplies a **declared** context
    instead of a queried one, and runs the identical composition."""

    installed_packs: frozenset[str] = frozenset()
    bound_agents: frozenset[str] = frozenset()
    known_archetypes: frozenset[str] = frozenset()


async def load_context(session: AsyncSession, org_id: UUID) -> ResolutionContext:
    """Read a real store's context. Assumes the caller already set the tenant context."""
    packs = set((await session.execute(_INSTALLED_PACKS_SQL, {"o": str(org_id)})).scalars().all())
    bound = set((await session.execute(_TENANT_AGENTS_SQL, {"o": str(org_id)})).scalars().all())
    known = set((await session.execute(text("SELECT slug FROM agent_archetypes"))).scalars().all())
    return ResolutionContext(frozenset(packs), frozenset(bound), frozenset(known))


def compose(
    *,
    config: PlanConfig,
    features: object,
    limits: EffectiveLimits,
    plan_id: UUID | None,
    plan_name: str | None,
    ctx: ResolutionContext,
    now: datetime,
    subscription_state: Literal["active", "cancelled", "none"] = "active",
) -> EffectiveEntitlements:
    """Compose effective entitlements from a plan and a store context. **Pure** — no I/O.

    Deterministic precedence: structured (`config.entitlements`) **or** legacy compatibility →
    agents → channels → promotions → alias canonicalisation → catalog/grantable filter →
    installed-pack filter → component-aware dependencies.

    A promotion may *add* a capability but never bypasses catalog validity, `runtime_grantable`,
    pack requirements, dependency requirements, or any security/approval/runtime gate.
    """
    excluded: list[Excluded] = []

    # --- 1. Capability source: structured config, or the legacy compatibility path -------------
    legacy_mode = not config.is_structured
    candidates: dict[str, Grant] = {}
    implied_channels: frozenset[str] = frozenset()

    if legacy_mode:
        legacy_caps = _LEGACY_ENT1A_BASELINE | normalize(features)
        for key in legacy_caps:
            candidates[key] = Grant(key, "legacy_compat")
        implied_channels = implied_legacy_channels(legacy_caps)
    elif not config.is_known_schema:
        excluded.append(Excluded(
            f"entitlement_schema_version={config.entitlement_schema_version}", "config",
            "unknown_entitlement_schema_version"))
    elif config.entitlements is None:
        # Structured plans never fall back to the legacy display column — fail closed instead.
        excluded.append(
            Excluded("entitlements", "config", "structured_plan_missing_entitlements"))
    else:
        for key in config.entitlements:
            if isinstance(key, str):
                candidates.setdefault(resolve_alias(key), Grant(resolve_alias(key), "plan"))

    structured_ok = legacy_mode or (config.is_known_schema and config.entitlements is not None)

    # --- 2. Agents: selected AND a real archetype AND bound for this tenant --------------------
    agents: set[str] = set()
    if structured_ok and config.agents:
        bound, known = set(ctx.bound_agents), set(ctx.known_archetypes)
        for slug in config.agents:
            if slug not in known:
                excluded.append(Excluded(slug, "agent", "unknown_archetype"))
            elif slug not in bound:
                # Globally valid but unsupported by this tenant's installed vertical pack.
                excluded.append(Excluded(slug, "agent", "no_tenant_binding"))
            else:
                agents.add(slug)

    # --- 3. Channels: plan selection only; connection/provider/live state is never consulted ---
    channels: set[str] = set(implied_channels)
    if structured_ok:
        for ch in config.channels:
            if ch in CHANNEL_TYPES:
                channels.add(ch)
            else:
                excluded.append(Excluded(ch, "channel", "unknown_channel_type"))

    # --- 4. Promotions: absolute UTC calendar windows, evaluated at read time ------------------
    if structured_ok:
        promos, promo_errors = parse_promotions(config.promotions)
        for err in promo_errors:
            excluded.append(Excluded(err, "promotion", "malformed_promotion"))
        for promo in promos:
            key = resolve_alias(promo.capability_key)
            if not promo.active_at(now):
                excluded.append(Excluded(key, "promotion", "promotion_not_active"))
                continue
            # A promotion only *adds* a candidate; every filter below still applies to it.
            candidates.setdefault(
                key, Grant(key, "promotion", promo.label, promo.ends_at))

    # --- 5. Catalog validity + runtime_grantable ----------------------------------------------
    installed = set(ctx.installed_packs)
    surviving: dict[str, Grant] = {}
    for key, grant in candidates.items():
        cap = by_key(key)
        if cap is None:
            excluded.append(Excluded(key, "capability", "not_in_catalog"))
            continue
        if not cap.runtime_grantable:
            reason = (
                f"governed_by:{cap.enforced_by}" if cap.enforced_by
                else f"not_grantable:{cap.status}")
            excluded.append(Excluded(key, "capability", reason))
            continue
        # --- 6. Installed-pack filter: global catalog knowledge is not tenant entitlement ------
        if cap.vertical is not None:
            if cap.vertical not in installed:
                excluded.append(Excluded(key, "capability", f"pack_not_installed:{cap.vertical}"))
                continue
        surviving[key] = grant

    # --- 7. Component-aware dependencies; iterate so a dropped key cascades deterministically --
    while True:
        keys = set(surviving)
        dropped = False
        for key in sorted(keys):
            cap = by_key(key)
            assert cap is not None
            for dep in cap.depends_on:
                ok, reason = dependency_satisfied(dep, keys, channels, agents)
                if not ok:
                    excluded.append(Excluded(key, "capability", reason))
                    del surviving[key]
                    dropped = True
                    break
        if not dropped:
            break

    return EffectiveEntitlements(
        capabilities=frozenset(surviving),
        agents=frozenset(agents),
        channels=frozenset(channels),
        limits=limits,
        grants=tuple(sorted(surviving.values(), key=lambda g: g.key)),
        excluded=tuple(excluded),
        subscription_state=subscription_state,
        plan_id=plan_id,
        plan_name=plan_name,
        addons=tuple(config.addons) if structured_ok else (),
    )


async def entitlements(session: AsyncSession, org_id: UUID) -> frozenset[str]:
    """The capabilities a store's plan currently grants. Thin wrapper over `resolve()` so
    `requires_feature` and `/v1/orgs/me` keep their existing shape."""
    return (await resolve(session, org_id)).capabilities


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


async def resolve(
    session: AsyncSession, org_id: UUID, *, now: datetime | None = None
) -> EffectiveEntitlements:
    """Resolve a real tenant's entitlements: load the subscription and store context, then compose.

    All the decision logic lives in `compose()`; this function only supplies facts from the
    database, so the operator preview and the runtime gate can never diverge."""
    now = now or datetime.now(UTC)
    await set_org_context(session, org_id)
    row = (await session.execute(_SUBSCRIPTION_SQL, {"o": str(org_id)})).mappings().first()

    ever = bool(row and row["existed"])
    if row is None or row["plan_id"] is None:
        # No paid entitlements without an active subscription. This is not "free Recover":
        # authentication and RBAC — not entitlements — govern account and data access.
        return EffectiveEntitlements(subscription_state="cancelled" if ever else "none")

    return compose(
        config=parse_plan_config(row["config"]),
        features=row["features"],
        limits=EffectiveLimits(int(row["max_managers"] or 0), int(row["max_staff"] or 0)),
        plan_id=row["plan_id"],
        plan_name=row["plan_name"],
        ctx=await load_context(session, org_id),
        now=now,
    )
