"""Enforcement inventory guard (PLAN-5).

This is the mechanism that stops a future feature becoming sellable-but-ungated. It is deliberately
stricter than "each capability has at least one gate" — partial coverage is the failure mode that
actually occurred (before PLAN-5, `campaigns.whatsapp` had two gated routes and five open ones), so
a per-capability check would have reported success.
"""

from __future__ import annotations

import pytest

from core.mediation.tools import REGISTRY, TOOL_CAPABILITY, TOOL_PLAN_EXEMPT
from core.tenancy.capabilities import by_key, catalog
from core.tenancy.enforcement import (
    MAPPED_PREFIXES,
    CapabilityEnforcement,
    Surface,
    all_surfaces,
    inventory,
    validate_inventory,
)


def test_the_inventory_is_complete_and_well_formed() -> None:
    assert validate_inventory() == []


def test_every_sellable_capability_is_represented() -> None:
    covered = {e.capability for e in inventory()}
    for cap in catalog():
        if cap.runtime_grantable:
            assert cap.key in covered, f"{cap.key} is sellable but has no declared surfaces"


@pytest.mark.parametrize("cap,surface", all_surfaces(), ids=lambda x: getattr(x, "id", x))
def test_every_surface_is_classified(cap: str, surface: Surface) -> None:
    """No surface may be `missing` or `unknown`: it is enforced with a named test, or explicitly
    exempt with a reason a reviewer can weigh."""
    if surface.enforcement:
        assert surface.test, f"{cap}/{surface.id} claims enforcement but names no test"
        assert not surface.exemption_reason
    else:
        assert surface.exemption_reason, f"{cap}/{surface.id} is neither enforced nor exempt"
        assert len(surface.exemption_reason) >= 20


def test_paid_execution_actions_are_never_exempt() -> None:
    """An exemption is only ever legitimate for reads, upkeep, privacy or maintenance."""
    for cap, s in all_surfaces():
        if s.exemption_reason and s.action in ("paid_compute", "external_effect", "automation"):
            pytest.fail(f"{cap}/{s.id} exempts a paid-execution action ({s.action})")


# ---- Mediation registry ------------------------------------------------------------------------


def test_every_registry_tool_declares_a_commercial_classification() -> None:
    """A tool must not be able to enter REGISTRY as a silent ungated execution path."""
    for name in REGISTRY:
        classified = name in TOOL_CAPABILITY or name in TOOL_PLAN_EXEMPT
        assert classified, f"{name} declares neither capability_key nor plan_exempt_reason"


def test_tool_capabilities_are_real_sellable_boundaries() -> None:
    for name, key in TOOL_CAPABILITY.items():
        cap = by_key(key)
        assert cap is not None, f"{name} → unknown capability {key!r}"
        assert cap.runtime_grantable, f"{name} → {key} is not an authorization boundary"


def test_tool_exemptions_carry_a_reviewable_reason() -> None:
    for name, reason in TOOL_PLAN_EXEMPT.items():
        assert len(reason) >= 20, f"{name} exemption reason is too thin to review"


def test_no_tool_is_both_gated_and_exempt() -> None:
    assert not (set(TOOL_CAPABILITY) & set(TOOL_PLAN_EXEMPT))


# ---- OpenAPI coverage --------------------------------------------------------------------------


def _declared_routes() -> dict[str, str]:
    """`"METHOD /path" -> surface id` for every bound route."""
    out: dict[str, str] = {}
    for _cap, surface in all_surfaces():
        for route in surface.routes:
            out[route] = surface.id
    return out


def _live_mapped_operations() -> set[str]:
    from core.api.main import app

    spec = app.openapi()
    return {
        f"{method.upper()} {path}"
        for path, ops in spec["paths"].items()
        for method in ops
        if path.startswith(MAPPED_PREFIXES)
    }


def test_every_live_mapped_route_is_bound_to_a_surface() -> None:
    """The guard that stops a new sellable route shipping unclassified: every operation under a
    mapped router must appear in the inventory, bound by exact method+path."""
    unclassified = sorted(_live_mapped_operations() - set(_declared_routes()))
    assert unclassified == [], (
        f"unclassified routes: {unclassified} — bind them in core/tenancy/enforcement.py")


def test_the_inventory_declares_no_route_that_does_not_exist() -> None:
    """Catches the opposite drift: a surface still claiming a route that was renamed or removed."""
    stale = sorted(
        route for route, _sid in _declared_routes().items()
        if route.split(" ", 1)[1].startswith(MAPPED_PREFIXES)
        and route not in _live_mapped_operations())
    assert stale == [], f"inventory references routes that no longer exist: {stale}"


def test_no_route_is_bound_to_two_surfaces() -> None:
    seen: dict[str, str] = {}
    for _cap, surface in all_surfaces():
        for route in surface.routes:
            assert route not in seen, f"{route} bound to both {seen[route]} and {surface.id}"
            seen[route] = surface.id


def test_the_route_guard_fails_when_a_new_route_appears() -> None:
    """Mutation proof: an unbound route must fail the guard, not slip through."""
    live = _live_mapped_operations() | {"POST /v1/campaigns/{campaign_id}/rogue"}
    assert sorted(live - set(_declared_routes())) == ["POST /v1/campaigns/{campaign_id}/rogue"]


# ---- Mutation: the guard must actually fail --------------------------------------------------


def test_the_guard_rejects_an_unclassified_surface() -> None:
    import core.tenancy.enforcement as mod

    original = mod.INVENTORY
    try:
        mod.INVENTORY = original + (
            CapabilityEnforcement("catalog", (Surface("http.rogue", "http", "mutation"),)),
        )
        mod._pack_inventory.cache_clear()
        problems = validate_inventory()
        assert any("neither enforced nor explicitly exempt" in p for p in problems), problems
    finally:
        mod.INVENTORY = original
        mod._pack_inventory.cache_clear()


def test_the_guard_rejects_enforcement_without_a_test() -> None:
    import core.tenancy.enforcement as mod

    original = mod.INVENTORY
    try:
        mod.INVENTORY = original + (
            CapabilityEnforcement("catalog", (
                Surface("http.untested", "http", "mutation", enforcement="requires_feature"),)),
        )
        assert any("without a named test" in p for p in validate_inventory())
    finally:
        mod.INVENTORY = original


def test_the_guard_rejects_a_sellable_capability_with_no_surfaces() -> None:
    import core.tenancy.enforcement as mod

    original = mod.INVENTORY
    try:
        mod.INVENTORY = tuple(e for e in original if e.capability != "landing_pages")
        problems = validate_inventory()
        assert any("landing_pages" in p and "absent" in p for p in problems), problems
    finally:
        mod.INVENTORY = original


def test_the_guard_rejects_an_unclassified_registry_tool() -> None:
    """Mutation proof for the tool guard: adding a REGISTRY entry without a classification fails."""
    import core.mediation.tools as tools

    original = dict(tools.REGISTRY)
    try:
        tools.REGISTRY["rogue.tool"] = original["catalog.search"]
        unclassified = [
            n for n in tools.REGISTRY
            if n not in tools.TOOL_CAPABILITY and n not in tools.TOOL_PLAN_EXEMPT
        ]
        assert unclassified == ["rogue.tool"]
    finally:
        tools.REGISTRY.clear()
        tools.REGISTRY.update(original)
