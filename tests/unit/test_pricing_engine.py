"""rules_v1 pricing engine (MVP-050) — golden cases + exactness properties.

Pure (no DB): loads both packs' `pricing/strategy.yaml` and runs the sample golden cases
through the SAME engine (the architecture guarantee), plus checks the money invariants —
integer minor units, no floats, per-stage residue, rounding modes, and stale-rate fail-closed.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from core.pricing import registry
from core.pricing.engine import Quote, compute, to_python
from core.pricing.functions import PricingError, fn_round

VERTICALS = Path(__file__).resolve().parents[2] / "verticals"
_JEWELRY = yaml.safe_load((VERTICALS / "jewelry" / "pricing" / "strategy.yaml").read_text())
_KIRANA = yaml.safe_load((VERTICALS / "kirana" / "pricing" / "strategy.yaml").read_text())

_RATES = {("ibja_gold", "22K"): 732000, ("ibja_gold", "18K"): 599000, ("ibja_silver", "925"): 9500}
_JEWELRY_PARAMS = {"making_pct": 8, "making_min_minor": 50000, "wastage_pct": 0,
                   "discount_ceiling_pct": 5}
_SNAP = uuid4()


def _jewelry(inputs: dict, *, rate_lookup=None) -> Quote:
    return compute(
        _JEWELRY["rules"], inputs, _JEWELRY_PARAMS,
        rate_lookup=rate_lookup or (lambda s, k: (_RATES[(s, k)], _SNAP)),
        tax_rules=registry.build_tax_rules(_JEWELRY),
        source_for=registry.build_source_for(_JEWELRY),
    )


def _lines(q: Quote) -> dict[str, int]:
    return {b["id"]: b["amount_minor"] for b in q.breakdown}


# ---- jewelry goldens ------------------------------------------------------------------


def test_pg001_full_breakdown_exact() -> None:
    q = _jewelry({"purity": "22K", "net_weight_g": "12.4", "stones": [],
                  "requested_discount_minor": 0})
    d = _lines(q)
    assert d["metal_value"] == 9076800 and d["making"] == 726144
    assert d["gst"] == 294088 and q.total_minor == 10097032
    # residue: components sum to the total exactly
    assert d["metal_value"] + d["wastage"] + d["making"] + d["stones"] - d["discount"] + d["gst"] \
        == q.total_minor
    assert _SNAP in q.rate_snapshot_ids  # provenance pinned


def test_pg002_making_min_floor() -> None:
    q = _jewelry({"purity": "22K", "net_weight_g": "0.5", "stones": [],
                  "requested_discount_minor": 0})
    assert _lines(q)["making"] == 50000  # floor applies


def test_pg031_silver_source_selected() -> None:
    q = _jewelry({"purity": "925", "net_weight_g": "210.0", "stones": [],
                  "requested_discount_minor": 0})
    assert _SNAP in q.rate_snapshot_ids  # ibja_silver resolved via source_for


def test_stones_projection_summed() -> None:
    q = _jewelry({"purity": "22K", "net_weight_g": "1.0",
                  "stones": [{"value_minor": 5000}, {"value_minor": 3000}],
                  "requested_discount_minor": 0})
    assert _lines(q)["stones"] == 8000  # sum(inputs.stones[].value_minor)


def test_pg014_discount_follows_formula_not_the_sample() -> None:
    # The sample golden pg-014 expects 239600 (5% of metal only); the authoritative formula
    # caps at 5% of the FULL subtotal (incl. making) → 258768. The engine follows the formula;
    # the sample is inconsistent (DECISIONS 2026-08-02).
    q = _jewelry({"purity": "18K", "net_weight_g": "8.0", "stones": [],
                  "requested_discount_minor": 1000000})
    assert _lines(q)["discount"] == 258768


def test_stale_rate_fails_closed() -> None:
    def stale(source: str, key: str) -> tuple[int, object]:
        raise PricingError("stale_rate", "rate older than staleness_max")

    with pytest.raises(PricingError) as ei:
        _jewelry({"purity": "22K", "net_weight_g": "12.4", "stones": []}, rate_lookup=stale)
    assert ei.value.code == "stale_rate"


# ---- kirana goldens (SAME engine, zero changes) ---------------------------------------

_KIRANA_PARAMS = {"delivery_fee_minor": 2000, "free_delivery_above_minor": 50000,
                  "delivery_radius_km": 3}
_CATALOG = {"atta5kg_27000": {"mrp_minor": 27000}, "maggi_1400": {"mrp_minor": 1400}}


def _kirana(inputs: dict) -> Quote:
    return compute(
        _KIRANA["rules"], inputs, _KIRANA_PARAMS,
        rate_lookup=lambda s, k: (0, None), item_lookup=lambda i: _CATALOG[i],
        offer_discount=lambda line: 0,
    )


def test_kpg01_free_delivery_threshold() -> None:
    q = _kirana({"lines": [{"item_id": "atta5kg_27000", "qty": 2}],
                 "delivery": True, "distance_km": "1.2"})
    d = _lines(q)
    assert d["subtotal"] == 54000 and d["delivery_fee"] == 0 and q.total_minor == 54000


def test_kpg02_delivery_fee_applied() -> None:
    q = _kirana({"lines": [{"item_id": "maggi_1400", "qty": 3}],
                 "delivery": True, "distance_km": "2.0"})
    d = _lines(q)
    assert d["subtotal"] == 4200 and d["delivery_fee"] == 2000 and q.total_minor == 6200


def test_kpg04_out_of_radius_guard() -> None:
    with pytest.raises(PricingError) as ei:
        _kirana({"lines": [{"item_id": "atta5kg_27000", "qty": 1}],
                 "delivery": True, "distance_km": "7.5"})
    assert ei.value.code == "delivery_out_of_radius"


# ---- engine properties ----------------------------------------------------------------


def test_rounding_modes() -> None:
    assert fn_round(Decimal("2.5"), "half_even") == 2 and fn_round(Decimal("3.5"), "half_even") == 4
    assert fn_round(Decimal("2.9"), "down") == 2 and fn_round(Decimal("2.1"), "up") == 3


def test_float_input_rejected() -> None:
    with pytest.raises(PricingError) as ei:
        _jewelry({"purity": "22K", "net_weight_g": 12.4, "stones": []})  # float, not str/Decimal
    assert ei.value.code == "config_schema_violation"


def test_residue_stage_fails_closed() -> None:
    # A money stage that doesn't resolve to an integer minor value is a residue → fail closed.
    rules = {"stages": [{"id": "total", "formula": "params.a / params.b"}]}
    with pytest.raises(PricingError) as ei:
        compute(rules, {}, {"a": 10, "b": 3}, rate_lookup=lambda s, k: (0, None))
    assert ei.value.code == "unledgered_figure"


def test_disallowed_syntax_rejected() -> None:
    rules = {"stages": [{"id": "total", "formula": "__import__('os').system('x')"}]}
    with pytest.raises(PricingError):
        compute(rules, {}, {}, rate_lookup=lambda s, k: (0, None))


def test_to_python_preprocessing() -> None:
    assert to_python("a && b || !c") == "a  and  b  or   not c"
    assert "for __p in inputs.stones" in to_python("sum(inputs.stones[].value_minor)")
    assert to_python("c ? a : b") == "((a) if (c) else (b))"
    assert to_python("map(xs, x, x.v)") == "[x.v for x in xs]"
