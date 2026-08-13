"""Canonical capability catalog (PLAN-1) — invariants, product truth, and the no-expansion rule.

These tests are the mechanism that stops marketing outrunning product: a capability cannot be
presented publicly unless its declared maturity supports it, and it cannot become an effective
tenant entitlement merely by being added to the catalog.
"""

from __future__ import annotations

import pytest

from core.tenancy import capabilities as cap
from core.tenancy.capabilities import (
    L0_CAPABILITIES,
    Capability,
    by_key,
    catalog,
    grantable_keys,
    public_capabilities,
    resolve_alias,
    validate_catalog,
)
from core.tenancy.entitlements import (
    ALL_FEATURES,
    BASELINE_FEATURES,
    GRANTABLE_FEATURES,
    LEGACY_EFFECTIVE_KEYS,
)

# The ENT-1a effective vocabulary, hard-coded as history. PLAN-1 may only *subtract* from this.
ENT1A_BASELINE = frozenset({"conversations", "catalog", "customers", "ghost_recovery"})
ENT1A_GRANTABLE = frozenset({
    "campaigns.whatsapp", "landing_pages", "ads.instagram", "ads.google", "seo", "agent.marketing",
})
UNSAFE_LEGACY_KEYS = frozenset({"seo", "agent.marketing", "ads.instagram", "ads.google"})


def test_catalog_satisfies_every_invariant() -> None:
    assert validate_catalog(catalog()) == []


@pytest.mark.parametrize("c", catalog(), ids=lambda c: c.key)
def test_each_capability_is_individually_well_formed(c: Capability) -> None:
    """Parametrized so a newly added capability is validated automatically."""
    assert validate_catalog((c,) + tuple(d for d in catalog() if d.key != c.key)) == []


def test_planned_capabilities_can_never_be_sold_or_granted() -> None:
    for c in catalog():
        if c.status == "planned":
            assert not c.runtime_grantable, c.key
            assert c.commercial_visibility == "planned", c.key


def test_partial_capabilities_are_never_publicly_presented() -> None:
    """Built-but-not-customer-reachable is not sellable — the strict end-to-end standard."""
    for c in catalog():
        if c.status == "partial":
            assert c.commercial_visibility in ("internal", "private_beta"), c.key


def test_public_capability_set_is_founder_approved() -> None:
    """The public **authorization-boundary / capability** set, not the pricing bullets.

    Several marketing bullets may ride on one capability (campaign analytics / ROI / growth
    analytics → `campaigns.analytics`; landing generation / lead capture / interest insights →
    `landing_pages`). Verifying the customer-facing Recover/Grow/Scale presentation is PLAN-3 and
    WEB work — do not re-add bullet assertions here.
    """
    approved: dict[str, tuple[str, str, bool]] = {
        # key: (status, commercial_visibility, runtime_grantable)
        "conversations":      ("available", "public", True),
        "catalog":            ("available", "public", True),
        "pricing":            ("available", "public", False),
        "customers":          ("available", "public", True),
        "ghost_recovery":     ("available", "public", True),
        "insights.business":  ("available", "public", False),
        "agent.concierge":    ("available", "public", False),
        "channel.whatsapp":   ("available", "public", False),
        "campaigns.whatsapp": ("available", "public", True),
        "campaigns.analytics": ("available", "public", True),
        "landing_pages":      ("available", "public", True),
        "catalog.ingestion":  ("available", "public", True),
        "seats":              ("available", "public", False),
        "jewelry.rate_operations": ("available", "public", True),
    }
    actual = {
        c.key: (c.status, c.commercial_visibility, c.runtime_grantable)
        for c in public_capabilities()
    }
    assert actual == approved


def test_capabilities_the_audit_found_not_end_to_end_stay_internal() -> None:
    """Zara, Mira, Nisha, Instagram and Google Ads are PARTIAL/INTERNAL, not public value."""
    for key in (
        "agent.nurture", "agent.campaigner", "agent.ops",
        "social.instagram_publishing", "ads.google",
    ):
        c = by_key(key)
        assert c is not None and c.commercial_visibility == "internal", key


def test_seats_does_not_create_a_second_enforcement_mechanism() -> None:
    """CP-3 remains the sole seat enforcement. The catalog only describes the limit."""
    seats = by_key("seats")
    assert seats is not None
    assert seats.kind == "limit"
    assert seats.runtime_grantable is False
    assert seats.enforced_by == "cp3_seat_limit"


def test_available_non_boundaries_name_what_governs_them() -> None:
    """A real capability that is not its own authorization boundary must point at the mechanism
    that already governs it, so we never build a redundant enforcement system."""
    for c in catalog():
        if c.status == "available" and not c.runtime_grantable:
            assert c.enforced_by is not None, c.key


# ---- The no-expansion rule ---------------------------------------------------------------------


def test_legacy_effective_set_is_exactly_ent1a_minus_the_unsafe_keys() -> None:
    """PLAN-1 is a vocabulary ticket. Deleting this test is the only way to widen authorization."""
    assert LEGACY_EFFECTIVE_KEYS == (ENT1A_BASELINE | ENT1A_GRANTABLE) - UNSAFE_LEGACY_KEYS


def test_effective_keys_are_a_subset_of_declared_boundaries() -> None:
    """The shim can only ever be narrower than the catalog, never wider."""
    assert LEGACY_EFFECTIVE_KEYS <= grantable_keys()


def test_catalog_declares_boundaries_that_are_not_yet_effective() -> None:
    """Proves the two concepts are genuinely separate: declared-grantable ≠ currently-effective."""
    assert {"campaigns.analytics", "catalog.ingestion"} <= grantable_keys()
    assert not {"campaigns.analytics", "catalog.ingestion"} & LEGACY_EFFECTIVE_KEYS


def test_ent1a_public_surface_still_holds() -> None:
    assert BASELINE_FEATURES.isdisjoint(GRANTABLE_FEATURES)
    assert ALL_FEATURES == BASELINE_FEATURES | frozenset(GRANTABLE_FEATURES)
    assert ALL_FEATURES == LEGACY_EFFECTIVE_KEYS


# ---- Vertical capabilities need pack context (PLAN-2 obligation) --------------------------------


def test_no_vertical_capability_is_effective_without_pack_context() -> None:
    """Global catalog knowledge is **not** tenant entitlement.

    A capability contributed by a vertical pack must never become effective for a tenant that has
    not installed that pack. `normalize()` has no org/pack context, so no L1 key may appear in the
    effective set. **PLAN-2 obligation:** the structured resolver must filter vertical capabilities
    against the tenant's installed/active packs before this can ever change.
    """
    for key in LEGACY_EFFECTIVE_KEYS:
        c = by_key(key)
        assert c is not None and c.vertical is None, f"{key} is a vertical capability"


def test_the_jewelry_contribution_loads_and_is_namespaced() -> None:
    c = by_key("jewelry.rate_operations")
    assert c is not None
    assert c.vertical == "jewelry"
    assert c.runtime_grantable is True   # eligible as a boundary…
    assert "jewelry.rate_operations" not in LEGACY_EFFECTIVE_KEYS  # …but not effective yet


def test_l0_catalog_contains_no_vertical_nouns() -> None:
    """Rule Zero: the vertical noun lives in the pack YAML, never in core."""
    import re

    words = set(
        re.findall(
            r"[a-z]+",
            " ".join(
                f"{c.key} {c.label} {c.description} {c.category}" for c in L0_CAPABILITIES
            ).lower(),
        )
    )
    for noun in ("gold", "karat", "jewel", "jewelry", "necklace", "diamond", "ring", "silver"):
        assert noun not in words, noun


# ---- Aliases ------------------------------------------------------------------------------------


def test_aliases_resolve_onto_real_capabilities() -> None:
    assert resolve_alias("ads.instagram") == "social.instagram_publishing"
    assert resolve_alias("landing_pages") == "landing_pages"   # pass-through
    assert resolve_alias("nonsense") == "nonsense"
    for legacy, canonical in cap.ALIASES.items():
        assert by_key(canonical) is not None
        assert by_key(legacy) is by_key(canonical)


def test_every_legacy_ent1a_key_still_resolves_to_a_catalog_entry() -> None:
    """Nothing from ENT-1a silently vanishes — each is understood, then judged on its merits."""
    for key in ENT1A_BASELINE | ENT1A_GRANTABLE:
        assert by_key(key) is not None, key


# ---- The catalog stays global -------------------------------------------------------------------


def test_catalog_module_is_org_independent() -> None:
    """It must not become org-scoped merely because it lives under core/tenancy.

    Tests the module's real shape — no public function accepts tenant context, and it pulls in no
    database machinery — rather than grepping source text, which its own docstring would trip.
    """
    import inspect

    for name, fn in inspect.getmembers(cap, inspect.isfunction):
        if name.startswith("_") or fn.__module__ != cap.__name__:
            continue
        params = set(inspect.signature(fn).parameters)
        assert not params & {"org_id", "session", "db", "current"}, f"{name}{tuple(params)}"

    # Module-level imports only, read from the AST so prose in the docstring cannot affect it.
    # (A lazy import inside `_pack_capabilities` is fine — that is the pack contract, not the DB.)
    import ast

    tree = ast.parse(inspect.getsource(cap))
    imported: set[str] = set()
    for node in tree.body:  # top level only
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert imported, "AST scan found no imports — the check would be vacuous"
    for banned in ("sqlalchemy", "core.tenancy.repository", "core.tenancy.middleware", "fastapi"):
        assert not any(m == banned or m.startswith(f"{banned}.") for m in imported), banned
