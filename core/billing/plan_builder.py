"""Operator Plan Builder — authoring structured commercial plans (PLAN-4).

The builder never invents product truth. What may be sold comes from the PLAN-1 catalog, how a plan
expresses authorization comes from the PLAN-2 structured contract, and what we already sell comes
from the PLAN-3 presets. This module only decides whether a *draft* is coherent, and shows the
operator what it would grant.

Two rules shape everything here:

*Registry existence is not sellability.* An archetype row or a channel-registry entry proves
something is technically wired; only the canonical catalog says it is a product we may charge for.
Both must agree, which is what keeps the partial nurture/campaigner/ops archetypes and the
Instagram/Google Ads channel types out of plans while they remain unfinished.

*Operator validation is not authorization.* Everything below is an authoring guard, deliberately
duplicated by nothing: dependency checks reuse the resolver's own `dependency_satisfied`, and the
preview runs the resolver's own `compose()`. Runtime enforcement remains PLAN-5's job, and a plan
saying "included" never by itself opens a route.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from core.channels.registry import CHANNEL_TYPES
from core.tenancy.capabilities import Capability, by_key, catalog, resolve_alias
from core.tenancy.entitlements import (
    EffectiveEntitlements,
    EffectiveLimits,
    ResolutionContext,
    compose,
    dependency_satisfied,
)
from core.tenancy.plan_config import PlanConfig, parse_promotions

#: A capability, agent or channel may only be sold when the catalog says it is finished *and*
#: presentable. `private_beta`, `internal`, `planned` and `partial` never qualify, and no request
#: parameter can relax this — elevating a restricted capability would need a privileged permission
#: and an audit trail, which is deliberately out of scope.
SELLABLE_STATUS = ("available", "beta")
SELLABLE_VISIBILITY = ("public", "public_beta")


def is_sellable(cap: Capability | None) -> bool:
    return (
        cap is not None
        and cap.status in SELLABLE_STATUS
        and cap.commercial_visibility in SELLABLE_VISIBILITY
    )


def selectable_capabilities(vertical: str | None = None) -> tuple[Capability, ...]:
    """Capabilities an operator may put in a plan for `vertical` (None = a generic plan).

    A vertical plan may take generic capabilities plus its own pack's; it may never take another
    vertical's, which would silently grant nothing at runtime and mislead the operator."""
    return tuple(
        c for c in catalog()
        if c.runtime_grantable and is_sellable(c)
        and (c.vertical is None or c.vertical == vertical)
    )


def selectable_agents(known_archetypes: frozenset[str]) -> tuple[str, ...]:
    """Archetypes that both exist technically and are commercially sellable."""
    return tuple(sorted(
        slug for slug in known_archetypes if is_sellable(by_key(f"agent.{slug}"))))


def selectable_channels() -> tuple[str, ...]:
    return tuple(sorted(
        slug for slug in CHANNEL_TYPES if is_sellable(by_key(f"channel.{slug}"))))


def public_capability_view(cap: Capability) -> dict[str, Any]:
    """Allow-list projection for the operator console.

    Built by naming what may leave, never by redacting a record: `evidence_refs` and `enforced_by`
    are internal product-truth bookkeeping and are simply absent."""
    return {
        "key": cap.key,
        "label": cap.label,
        "description": cap.description,
        "category": cap.category,
        "kind": cap.kind,
        "status": cap.status,
        "commercial_visibility": cap.commercial_visibility,
        "depends_on": list(cap.depends_on),
        "vertical": cap.vertical,
    }


# ---- Draft validation ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Problem:
    field: str
    key: str
    reason: str
    fix_hint: str = ""  # what the operator could explicitly add; never applied automatically


@dataclass
class _Acc:
    items: list[Problem] = field(default_factory=list)

    def add(self, f: str, k: str, r: str, hint: str = "") -> None:
        self.items.append(Problem(f, k, r, hint))


def validate_draft(
    config: PlanConfig,
    *,
    known_archetypes: frozenset[str],
    max_managers: int = 0,
    max_staff: int = 0,
) -> list[Problem]:
    """Every reason a draft may not be saved. An empty list means it is coherent.

    Dependencies are checked with the resolver's own rule, so the builder can never bless a
    combination the runtime would then reject. Missing dependencies are **reported with a hint**,
    never added — an invisible grant is exactly what we refuse to create.
    """
    acc = _Acc()

    if config.entitlement_schema_version != 1:
        acc.add("config", "entitlement_schema_version", "must be 1 for a structured plan")
    if max_managers < 0 or max_staff < 0:
        acc.add("limits", "seats", "seat limits cannot be negative")

    vertical = config.vertical
    if vertical is not None and not any(c.vertical == vertical for c in catalog()):
        acc.add("vertical", vertical, "no installed pack contributes commercial capabilities here")

    caps: set[str] = set()
    for raw in config.entitlements or []:
        key = resolve_alias(raw)
        cap = by_key(key)
        if cap is None:
            acc.add("entitlements", raw, "not in the canonical catalog")
            continue
        if not cap.runtime_grantable:
            acc.add("entitlements", key,
                    f"not an authorization boundary (governed by {cap.enforced_by or 'RBAC'})",
                    "represent it through agents/channels/limits instead")
            continue
        if not is_sellable(cap):
            acc.add("entitlements", key,
                    f"not sellable — status={cap.status}, visibility={cap.commercial_visibility}")
            continue
        if cap.vertical is not None and cap.vertical != vertical:
            acc.add("entitlements", key,
                    f"belongs to the {cap.vertical!r} vertical; this plan targets {vertical!r}",
                    f"set the plan's vertical to {cap.vertical!r}")
            continue
        caps.add(key)

    agents: set[str] = set()
    for slug in config.agents:
        if slug not in known_archetypes:
            acc.add("agents", slug, "no such archetype")
        elif not is_sellable(by_key(f"agent.{slug}")):
            cap = by_key(f"agent.{slug}")
            detail = f"status={cap.status}" if cap else "no catalog entry"
            acc.add("agents", slug, f"not sellable ({detail})")
        else:
            agents.add(slug)

    channels: set[str] = set()
    for slug in config.channels:
        if slug not in CHANNEL_TYPES:
            acc.add("channels", slug, "not a registered channel type")
        elif not is_sellable(by_key(f"channel.{slug}")):
            acc.add("channels", slug, "registered but not commercially sellable")
        else:
            channels.add(slug)

    # Dependencies — the resolver's rule, so authoring and runtime cannot disagree.
    for key in sorted(caps):
        cap = by_key(key)
        assert cap is not None
        for dep in cap.depends_on:
            ok, reason = dependency_satisfied(dep, caps, channels, agents)
            if not ok:
                dep_cap = by_key(dep)
                hint = ""
                if dep_cap is not None and dep_cap.kind == "channel":
                    hint = f"add the {dep.removeprefix('channel.')!r} channel"
                elif dep_cap is not None and dep_cap.runtime_grantable:
                    hint = f"add the {dep!r} capability"
                acc.add("entitlements", key, reason, hint)

    promos, promo_errors = parse_promotions(config.promotions)
    for err in promo_errors:
        acc.add("promotions", err, "malformed promotion")
    for promo in promos:
        key = resolve_alias(promo.capability_key)
        cap = by_key(key)
        if cap is None or not cap.runtime_grantable or not is_sellable(cap):
            acc.add("promotions", key, "only sellable authorization boundaries may be promoted")
            continue
        if cap.vertical is not None and cap.vertical != vertical:
            acc.add("promotions", key, f"belongs to the {cap.vertical!r} vertical")
            continue
        for dep in cap.depends_on:
            ok, reason = dependency_satisfied(dep, caps | {key}, channels, agents)
            if not ok:
                acc.add("promotions", key, reason)

    return acc.items


# ---- Preview --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Preview:
    effective: EffectiveEntitlements
    assumptions: tuple[str, ...]


def preview_draft(
    config: PlanConfig,
    *,
    known_archetypes: frozenset[str],
    max_managers: int = 0,
    max_staff: int = 0,
    plan_name: str | None = None,
    now: datetime | None = None,
) -> Preview:
    """What this plan would grant, computed with the resolver's own composition.

    A plan under construction belongs to no tenant, so rather than fabricating a tenant and a
    throwaway subscription just to reuse `resolve()`, the store-side facts are **declared**: the
    plan's vertical pack is assumed installed and its selected agents assumed provisioned. Those
    assumptions are returned alongside the result so nobody reads a preview as a runtime guarantee.
    """
    ctx = ResolutionContext(
        installed_packs=frozenset({config.vertical} if config.vertical else set()),
        bound_agents=frozenset(config.agents),
        known_archetypes=known_archetypes,
    )
    effective = compose(
        config=config,
        features=[],  # a structured plan never consults the legacy display column
        limits=EffectiveLimits(max_managers, max_staff),
        plan_id=None,
        plan_name=plan_name,
        ctx=ctx,
        now=now or datetime.now(UTC),
    )
    assumptions = [
        "the store's agents for the selected archetypes are provisioned",
        "runtime enforcement of individual routes remains subject to PLAN-5",
    ]
    if config.vertical:
        assumptions.insert(0, f"the {config.vertical!r} vertical pack is installed and active")
    return Preview(effective, tuple(assumptions))
