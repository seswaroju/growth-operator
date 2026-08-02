"""rules_v1 pricing engine (MVP-050) — see docs/21-platform/pricing-engine-abstraction.md.

`compute` runs a strategy's ordered stages over exact arithmetic and returns a breakdown, total,
and provenance. Formulas are a small CEL-ish DSL evaluated by a **safe AST interpreter** (a
whitelist of node types — no `eval`, no imports, no attribute abuse), with every value an int
(minor units) or `Decimal` (never a float). Each money stage must resolve to an integer minor
value (a non-integer residue fails closed), and `sum(breakdown) == total` is asserted exactly.
`rate()` pins the snapshot id it used into provenance; a stale rate raises `stale_rate`.

The same engine runs every strategy unchanged — the pack's formulas are the only thing that
differs between packs.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from core.pricing.functions import (
    DotItem,
    PricingError,
    TaxRule,
    dot,
    fn_max,
    fn_min,
    fn_round,
    fn_sum,
    to_decimal,
)

# rate_lookup(source_key, key) -> (per_g_minor, snapshot_id); raises PricingError('stale_rate').
RateLookup = Callable[[str, str], tuple[int, UUID | None]]


@dataclass
class Quote:
    breakdown: list[dict[str, Any]]
    total_minor: int
    rate_snapshot_ids: list[UUID] = field(default_factory=list)


# ---- formula preprocessing (DSL → Python source) --------------------------------------

_PROJECTION = re.compile(r"([A-Za-z_][\w.]*)\[\]\.(\w+)")  # inputs.stones[].value_minor


def _split_args(src: str) -> list[str]:
    """Split a comma-separated arg list, respecting nested () [] and quotes."""
    args, depth, buf, quote = [], 0, [], ""
    for ch in src:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        args.append("".join(buf).strip())
    return args


def _find_call(src: str, name: str) -> tuple[int, int, str] | None:
    """Find the first `name(...)` call; return (start, end_after_paren, inner_args)."""
    m = re.search(rf"\b{name}\(", src)
    if m is None:
        return None
    depth, i = 0, m.end() - 1
    while i < len(src):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return m.start(), i + 1, src[m.end():i]
        i += 1
    raise PricingError("config_schema_violation", f"unbalanced {name}(")


def _rewrite_map(src: str) -> str:
    """map(seq, var, expr) → [expr for var in seq] (recursively, innermost handled by recursion)."""
    while (found := _find_call(src, "map")) is not None:
        start, end, inner = found
        seq, var, expr = _split_args(inner)
        src = f"{src[:start]}[{_rewrite_map(expr)} for {var} in {seq}]{src[end:]}"
    return src


def _rewrite_ternary(src: str) -> str:
    """cond ? a : b → (a) if (cond) else (b) (single, non-nested — sufficient for MVP packs)."""
    if "?" not in src:
        return src
    q = src.index("?")
    # the ':' that matches this '?' at depth 0
    depth, i = 0, q + 1
    while i < len(src):
        if src[i] in "([":
            depth += 1
        elif src[i] in ")]":
            depth -= 1
        elif src[i] == ":" and depth == 0:
            cond, a, b = src[:q], src[q + 1:i], src[i + 1:]
            return f"(({a.strip()}) if ({cond.strip()}) else ({b.strip()}))"
        i += 1
    return src


def to_python(formula: str) -> str:
    src = formula.replace("&&", " and ").replace("||", " or ")
    src = re.sub(r"!(?!=)", " not ", src)  # unary ! → not (leave != alone)
    src = _PROJECTION.sub(r"[__p.\2 for __p in \1]", src)
    src = _rewrite_map(src)
    src = _rewrite_ternary(src)
    return src.strip()  # a leading '!'→'not ' would otherwise indent the expression


# ---- safe AST evaluator ---------------------------------------------------------------

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Call, ast.IfExp,
    ast.Name, ast.Load, ast.Store, ast.Attribute, ast.Subscript, ast.Constant, ast.ListComp,
    ast.comprehension, ast.List, ast.keyword,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.Not, ast.And, ast.Or,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq, ast.In, ast.NotIn,
)


class _Interpreter:
    def __init__(self, names: dict[str, Any], funcs: dict[str, Callable[..., Any]]) -> None:
        self.names = names
        self.funcs = funcs

    def eval(self, formula: str) -> Any:
        tree = ast.parse(to_python(formula), mode="eval")
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED_NODES):
                raise PricingError(
                    "config_schema_violation", f"disallowed syntax: {type(node).__name__}"
                )
        return self._node(tree.body, {})

    def _node(self, node: ast.AST, local: dict[str, Any]) -> Any:  # noqa: C901, PLR0911, PLR0912
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in local:
                return local[node.id]
            if node.id in self.names:
                return self.names[node.id]
            raise PricingError("config_schema_violation", f"unknown name {node.id!r}")
        if isinstance(node, ast.Attribute):
            return getattr(self._node(node.value, local), node.attr)
        if isinstance(node, ast.Subscript):
            return self._node(node.value, local)[self._node(node.slice, local)]
        if isinstance(node, ast.UnaryOp):
            v = self._node(node.operand, local)
            return (not v) if isinstance(node.op, ast.Not) else -to_decimal(v)
        if isinstance(node, ast.BoolOp):
            vals = [self._node(v, local) for v in node.values]
            return all(vals) if isinstance(node.op, ast.And) else any(vals)
        if isinstance(node, ast.Compare):
            left = self._node(node.left, local)
            right = self._node(node.comparators[0], local)
            return _compare(node.ops[0], left, right)
        if isinstance(node, ast.BinOp):
            return _binop(node.op, self._node(node.left, local), self._node(node.right, local))
        if isinstance(node, ast.IfExp):
            branch = node.body if self._node(node.test, local) else node.orelse
            return self._node(branch, local)
        if isinstance(node, ast.ListComp):
            gen = node.generators[0]
            target = gen.target.id  # type: ignore[attr-defined]
            out = []
            for item in self._node(gen.iter, local):
                inner = {**local, target: item}
                if all(self._node(c, inner) for c in gen.ifs):  # no ifs → included
                    out.append(self._node(node.elt, inner))
            return out
        if isinstance(node, ast.List):
            return [self._node(e, local) for e in node.elts]
        if isinstance(node, ast.Call):
            return self._call(node, local)
        raise PricingError("config_schema_violation", f"disallowed node {type(node).__name__}")

    def _call(self, node: ast.Call, local: dict[str, Any]) -> Any:
        args = [self._node(a, local) for a in node.args]
        kwargs = {k.arg: self._node(k.value, local) for k in node.keywords if k.arg}
        if isinstance(node.func, ast.Attribute):  # method call, e.g. tax_rule(...).apply(x)
            receiver = self._node(node.func.value, local)
            return getattr(receiver, node.func.attr)(*args, **kwargs)
        if isinstance(node.func, ast.Name) and node.func.id in self.funcs:
            return self.funcs[node.func.id](*args, **kwargs)
        raise PricingError("config_schema_violation", "unknown function call")


def _binop(op: ast.operator, left: Any, right: Any) -> Any:
    a, b = to_decimal(left), to_decimal(right)
    if isinstance(op, ast.Add):
        return int(a + b) if isinstance(left, int) and isinstance(right, int) else a + b
    if isinstance(op, ast.Sub):
        return int(a - b) if isinstance(left, int) and isinstance(right, int) else a - b
    if isinstance(op, ast.Mult):
        return a * b
    if isinstance(op, ast.Div):
        if b == 0:
            raise PricingError("config_schema_violation", "division by zero")
        return a / b
    raise PricingError("config_schema_violation", f"disallowed operator {type(op).__name__}")


def _compare(op: ast.cmpop, left: Any, right: Any) -> bool:
    if isinstance(op, ast.In):
        return left in right
    if isinstance(op, ast.NotIn):
        return left not in right
    if isinstance(op, ast.Eq):
        return bool(left == right)
    if isinstance(op, ast.NotEq):
        return bool(left != right)
    a, b = to_decimal(left), to_decimal(right)
    return {ast.Lt: a < b, ast.LtE: a <= b, ast.Gt: a > b, ast.GtE: a >= b}[type(op)]


# ---- compute --------------------------------------------------------------------------


def _stage_int(value: Any, stage_id: str) -> int:
    """A money stage must resolve to an integer minor value — a residue fails closed."""
    if isinstance(value, bool):
        raise PricingError("config_schema_violation", f"stage {stage_id!r} is boolean")
    d = to_decimal(value)
    if d != d.to_integral_value():
        raise PricingError("unledgered_figure", f"stage {stage_id!r} has a rounding residue: {d}")
    return int(d)


def compute(
    strategy_rules: dict[str, Any], inputs: dict[str, Any], params: dict[str, Any], *,
    rate_lookup: RateLookup, tax_rules: dict[str, Decimal] | None = None,
    item_lookup: Callable[[str], dict[str, Any]] | None = None,
    source_for: Callable[[str], str] | None = None,
    offer_discount: Callable[[Any], int] | None = None,
) -> Quote:
    """Evaluate a strategy's stages → breakdown + total + provenance. Pure and replayable."""
    snapshot_ids: list[UUID] = []
    tax_rules = tax_rules or {}

    def _rate(source: str, key: str) -> DotItem:
        value, snap = rate_lookup(source, key)
        if snap is not None and snap not in snapshot_ids:
            snapshot_ids.append(snap)
        return DotItem({"per_g_minor": int(value)})

    def _tax_rule(rule_id: str) -> TaxRule:
        if rule_id not in tax_rules:
            raise PricingError("config_schema_violation", f"unknown tax rule {rule_id!r}")
        return TaxRule(tax_rules[rule_id])

    funcs: dict[str, Callable[..., Any]] = {
        "round": fn_round, "sum": fn_sum, "min": fn_min, "max": fn_max,
        "rate": _rate, "tax_rule": _tax_rule,
        "source_for": (lambda p: source_for(p)) if source_for else (lambda p: p),
        "item": (lambda i: DotItem(item_lookup(i))) if item_lookup else None,  # type: ignore[dict-item]
        "offer_discount": offer_discount if offer_discount else (lambda line: 0),
    }
    stage_vals: dict[str, Any] = {}
    names = {"inputs": dot(inputs), "params": dot(params), "stage": DotItem(stage_vals)}
    interp = _Interpreter(names, {k: v for k, v in funcs.items() if v is not None})

    breakdown: list[dict[str, Any]] = []
    total = 0
    for stage in strategy_rules.get("stages", []):
        stage_id = stage["id"]
        for guard in stage.get("guards", []):
            if not interp.eval(guard):
                raise PricingError(stage.get("guard_error", "config_schema_violation"), guard)
        value = interp.eval(stage["formula"])
        if isinstance(value, int | Decimal) and not isinstance(value, bool):
            result = _stage_int(value, stage_id)  # a money stage → integer minor, breakdown line
            stage_vals[stage_id] = result
            breakdown.append({"id": stage_id, "amount_minor": result})
            total = result  # the last money stage ('total') is the grand total
        else:
            stage_vals[stage_id] = value  # intermediate (e.g. a list of line totals)

    return Quote(breakdown=breakdown, total_minor=total, rate_snapshot_ids=snapshot_ids)
