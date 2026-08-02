"""Availability transition graph + rule-AST price-input extractor (MVP-049).

No database: the transition graph is constant, and dependency extraction is a pure walk over the
strategy's stage ASTs. Proves the extractor finds exactly the catalog inputs each jewelry stage
reads (and that the same extractor works for the kirana pack — nothing jewelry-specific).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from core.catalog.availability import (
    ALLOWED_TRANSITIONS,
    AVAILABILITY_STATES,
    _input_refs,
    price_input_deps,
)

VERTICALS = Path(__file__).resolve().parents[2] / "verticals"


def _strategy(pack: str) -> dict:
    return yaml.safe_load((VERTICALS / pack / "pricing" / "strategy.yaml").read_text())


def test_jewelry_price_input_deps_are_the_priced_attributes() -> None:
    assert price_input_deps(_strategy("jewelry")) == {
        "net_weight_g", "purity", "stones", "requested_discount_minor",
    }


def test_jewelry_extractor_per_stage() -> None:
    stages = {s["id"]: _input_refs(s["formula"]) for s in _strategy("jewelry")["rules"]["stages"]}
    assert stages["metal_value"] == {"net_weight_g", "purity"}
    assert stages["stones"] == {"stones"}
    assert stages["discount"] == {"requested_discount_minor"}
    assert stages["making"] == set()   # reads earlier stages + params, no catalog input
    assert stages["gst"] == set()
    assert stages["total"] == set()


def test_extractor_is_pack_agnostic() -> None:
    # The same AST walk yields the kirana pack's own inputs — no jewelry knowledge baked in.
    deps = price_input_deps(_strategy("kirana"))
    assert deps and "net_weight_g" not in deps  # kirana prices lines/delivery, not gold weight


def test_transition_graph_is_closed_over_known_states() -> None:
    for src, targets in ALLOWED_TRANSITIONS.items():
        assert src in AVAILABILITY_STATES
        assert targets <= AVAILABILITY_STATES
    # bookable_slot is out of MVP scope: never a source or a target.
    assert "bookable_slot" not in ALLOWED_TRANSITIONS
    assert all("bookable_slot" not in t for t in ALLOWED_TRANSITIONS.values())
