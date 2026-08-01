"""Catalog attribute validation (MVP-046) — JSON Schema + CEL, against the real jewelry schema.

Pure (no DB): loads verticals/jewelry/catalog/schema.json and checks path-precise structural
errors + cross-field CEL constraints with their exact messages.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.catalog.validate import validate_attributes

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "verticals/jewelry/catalog/schema.json").read_text()
)

_VALID = {
    "category": "chain", "metal": "gold", "purity": "22K",
    "gross_weight_g": 10.0, "net_weight_g": 9.0,
}


def _problems(attrs: dict) -> list:
    return validate_attributes(attrs, json_schema=SCHEMA, cache_key="jewelry:2")


def test_valid_item_has_no_problems() -> None:
    assert _problems(_VALID) == []


def test_net_greater_than_gross_exact_message() -> None:
    probs = _problems({**_VALID, "gross_weight_g": 5.0, "net_weight_g": 9.0})
    assert len(probs) == 1
    assert probs[0].error == "net weight cannot exceed gross weight"
    assert "net_weight_g" in probs[0].rule and probs[0].rule != "schema"


def test_gold_must_be_karat_constraint() -> None:
    probs = _problems({**_VALID, "purity": "925"})  # 925 is a valid enum but not karat for gold
    assert any(p.error == "gold purity must be a karat value" for p in probs)


def test_unknown_attribute_rejected() -> None:
    probs = _problems({**_VALID, "bogus_field": "x"})
    assert probs and all(p.rule == "schema" for p in probs)
    assert any("bogus_field" in p.error or "bogus_field" in p.path for p in probs)


def test_missing_required_is_path_precise() -> None:
    attrs = {k: v for k, v in _VALID.items() if k != "category"}
    probs = _problems(attrs)
    assert probs and any(p.rule == "schema" and "category" in p.error for p in probs)


def test_enum_violation_reported() -> None:
    probs = _problems({**_VALID, "metal": "wood"})
    assert probs and any(p.rule == "schema" and p.path.endswith("metal") for p in probs)


def test_structural_errors_shortcircuit_cel() -> None:
    # A bad shape (missing net_weight_g) returns only structural errors, not a CEL crash.
    attrs = {k: v for k, v in _VALID.items() if k != "net_weight_g"}
    assert all(p.rule == "schema" for p in _problems(attrs))
