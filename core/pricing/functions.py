"""Registered pricing functions + value helpers (MVP-050).

The rules_v1 engine evaluates pack formulas over **exact** arithmetic — every value is an int
(minor units) or a `Decimal`, never a float — with rounding declared per stage. These are the
functions a formula may call (`round`, `sum`, `min`, `max`, `rate`, `source_for`, `tax_rule`,
`map`, `item`, `offer_discount`) plus `DotItem`, which gives formulas attribute access
(`inputs.net_weight_g`, `rate(...).per_g_minor`) over plain dicts/lists.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_UP, Decimal
from typing import Any

Number = int | Decimal

_ROUND_MODES = {
    "half_even": ROUND_HALF_EVEN,
    "half_up": ROUND_HALF_UP,
    "down": ROUND_DOWN,
    "up": ROUND_UP,
}


class PricingError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def to_decimal(value: Any) -> Decimal:
    """Exact Decimal from an int/str/Decimal — floats are rejected (the money invariant)."""
    if isinstance(value, bool):  # bool is an int subclass — keep it out of arithmetic
        raise PricingError("config_schema_violation", "boolean where a number was expected")
    if isinstance(value, float):
        raise PricingError("config_schema_violation", f"float {value!r} in pricing (use Decimal)")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def dot(value: Any) -> Any:
    """Wrap dicts/lists so formulas can use attribute access; numbers become exact."""
    if isinstance(value, DotItem):
        return value
    if isinstance(value, dict):
        return DotItem(value)
    if isinstance(value, list):
        return [dot(v) for v in value]
    if isinstance(value, float):
        return to_decimal(value)  # never allow a float to leak into a formula
    return value


class DotItem:
    """Attribute + item access over a dict, converting nested values via `dot`."""

    def __init__(self, data: dict[str, Any]) -> None:
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name: str) -> Any:
        try:
            return dot(self._data[name])
        except KeyError as exc:
            raise PricingError("config_schema_violation", f"unknown field {name!r}") from exc

    def __getitem__(self, key: str) -> Any:
        return self.__getattr__(key)

    def get(self, key: str, default: Any = None) -> Any:
        return dot(self._data[key]) if key in self._data else default


# ---- functions callable from formulas -------------------------------------------------


def fn_round(value: Number, mode: str = "half_even") -> int:
    quant = to_decimal(value).quantize(Decimal(1), rounding=_ROUND_MODES[mode])
    return int(quant)


def fn_sum(values: list[Number]) -> int:
    total = Decimal(0)
    for v in values:
        total += to_decimal(v)
    return int(total)


def fn_min(*values: Number) -> Number:
    return min(values, key=to_decimal)


def fn_max(*values: Number) -> Number:
    return max(values, key=to_decimal)


class TaxRule:
    """A tax rule resolved by id; `apply(base)` returns the rounded tax on `base`."""

    def __init__(self, pct: Number) -> None:
        self._pct = to_decimal(pct)

    def apply(self, base: Number) -> int:
        return fn_round(to_decimal(base) * self._pct / Decimal(100), "half_even")
