"""Pack commercial-capability contract + evidence drift (PLAN-1).

`evidence_refs` is audit metadata, never authorization — but stale metadata is worthless, so the
refs are checked against the routes the application actually registers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.packs.bundle import parse_pack_dir
from core.tenancy.capabilities import catalog, public_capabilities, validate_catalog

REPO = Path(__file__).resolve().parents[2]
VERTICALS = REPO / "verticals"


@pytest.fixture(scope="module")
def registered_routes() -> set[str]:
    from core.api.main import app

    spec = app.openapi()
    return {
        f"{method.upper()} {path}"
        for path, ops in spec["paths"].items()
        for method in ops
    }


def test_every_evidence_ref_matches_a_registered_route(registered_routes: set[str]) -> None:
    """Catches the exact class of error a hand-written route string invites: a ref that is stale,
    aspirational, or a typo. (`/v1/landing/public/{slug}/lead` never existed; the real public
    lead route is `POST /p/{page_id}/lead`.)"""
    missing = [
        (c.key, ref)
        for c in catalog()
        for ref in c.evidence_refs
        if ref not in registered_routes
    ]
    assert missing == [], f"evidence refs with no registered route: {missing}"


def test_public_capabilities_carry_evidence(registered_routes: set[str]) -> None:
    for c in public_capabilities():
        if c.kind == "limit":
            continue  # a seat limit has no route; CP-3 enforces it
        assert c.evidence_refs, c.key


def test_jewelry_pack_parses_its_commercial_section() -> None:
    parsed = parse_pack_dir(VERTICALS / "jewelry")
    assert parsed.commercial is not None
    keys = {c.key for c in parsed.commercial.capabilities}
    assert "rate_operations" in keys  # un-namespaced on disk; namespaced by the catalog loader


def test_a_pack_without_a_commercial_section_still_loads() -> None:
    """The manifest key is optional — kirana contributes nothing and must remain valid."""
    parsed = parse_pack_dir(VERTICALS / "kirana")
    assert parsed.commercial is None


def test_pack_capabilities_are_namespaced_and_cannot_shadow_l0(tmp_path: Path) -> None:
    """A pack declaring `catalog` yields `demo.catalog`, never a shadowed L0 capability."""
    from core.tenancy.capabilities import _pack_capabilities

    pack = tmp_path / "demo"
    (pack / "commercial").mkdir(parents=True)
    (pack / "pack.yaml").write_text(
        yaml.safe_dump({"pack": "demo", "commercial": "commercial/capabilities.yaml"})
    )
    (pack / "commercial" / "capabilities.yaml").write_text(
        yaml.safe_dump({"capabilities": [{
            "key": "catalog", "label": "X", "description": "Y", "category": "operations",
            "kind": "feature", "status": "planned", "commercial_visibility": "planned",
        }]})
    )
    caps = _pack_capabilities(tmp_path)
    assert [c.key for c in caps] == ["demo.catalog"]
    assert caps[0].vertical == "demo"


def test_a_pack_may_not_declare_a_planned_capability_as_sellable(tmp_path: Path) -> None:
    """The invariants apply to L1 contributions exactly as they do to L0."""
    from core.tenancy.capabilities import _pack_capabilities

    pack = tmp_path / "demo"
    (pack / "commercial").mkdir(parents=True)
    (pack / "pack.yaml").write_text(
        yaml.safe_dump({"pack": "demo", "commercial": "commercial/capabilities.yaml"})
    )
    (pack / "commercial" / "capabilities.yaml").write_text(
        yaml.safe_dump({"capabilities": [{
            "key": "vapour", "label": "X", "description": "Y", "category": "growth",
            "kind": "feature", "status": "planned", "commercial_visibility": "public",
            "runtime_grantable": True,
        }]})
    )
    problems = validate_catalog(_pack_capabilities(tmp_path))
    assert any("planned but runtime_grantable" in p for p in problems)
    assert any("planned but visibility is not" in p for p in problems)
