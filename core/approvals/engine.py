"""Approval policy engine (MVP-065).

`evaluate(session, ctx)` gives every side effect a **deterministic tier** from declarative rules:
load the rules for an `action_type` — core tier-4 minimums (platform code) → pack defaults →
tenant rows (tighten-only) → any active incident-tightening — evaluate each rule's CEL against the
`ActionContext`, and take the **max tier** (matched rule ids recorded). Result is order-independent
(same ctx → same decision across any rule ordering). CEL programs compile once per expression and
are cached, keeping evaluation inside the 5 ms budget.

Token minting (tier ≥ 1 ⇒ `execution_token`) and the approval object lifecycle are **MVP-066** —
this ticket delivers the evaluator, the compile cache, the tighten-only validator, and the wiring
that makes the mediation tier check live. `validate_tenant_rule` rejects a tenant rule that would
lower a tier below the core/pack baseline (a tenant may only tighten).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import celpy
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.errors import GrowthOperatorError
from core.tenancy import repository

# Platform-invariant never-autonomous actions (L0 — no pack/tenant may lower these).
CORE_TIER4_ACTIONS: frozenset[str] = frozenset({
    "payment.charge", "payment.refund", "payout.create", "supplier.order_commit",
    "gbp.update", "ads.publish",
})
# A tier-eval action with no matching rule fails safe to "needs approval".
DEFAULT_UNKNOWN_TIER = 2
NEVER_AUTONOMOUS_TIER = 4


@dataclass
class ActionContext:
    org_id: UUID
    action_type: str
    actor_instance_id: UUID | None = None
    amount_minor: int | None = None
    currency: str | None = None
    recipients: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    untrusted_content: bool = False


@dataclass
class Decision:
    tier: int
    matched_rules: list[str]
    approver_chain: list[Any] = field(default_factory=list)
    timeout_s: int | None = None
    on_timeout: str = "hold"
    confirm_kind: str | None = None


_ENV = celpy.Environment()
_PROGRAM_CACHE: dict[str, Any] = {}  # cel_expr -> compiled program (per-process, expr is the key)


def _program(cel_expr: str) -> Any:
    prog = _PROGRAM_CACHE.get(cel_expr)
    if prog is None:
        prog = _ENV.program(_ENV.compile(cel_expr))
        _PROGRAM_CACHE[cel_expr] = prog
    return prog


def _activation(ctx: ActionContext) -> dict[str, Any]:
    data: dict[str, Any] = {
        "action_type": ctx.action_type,
        "amount_minor": int(ctx.amount_minor or 0),
        "currency": ctx.currency or "",
        "recipients": list(ctx.recipients),
        "attributes": ctx.attributes,
        "untrusted_content": bool(ctx.untrusted_content),
    }
    return {k: celpy.json_to_cel(v) for k, v in data.items()}


def _matches(cel_expr: str | None, activation: dict[str, Any]) -> bool:
    """A rule with no expr (or 'true') always matches. A rule whose CEL cannot be **compiled or
    evaluated** FAILS SAFE: it is treated as matching, so its declared `tier` still contributes.
    Because the engine takes the max tier, an unresolved guard can only tighten, never loosen — a
    broken tightening rule is never silently dropped. (Pack rules are compile-checked at
    certification and tenant rules come from templates, so this is defence-in-depth.)"""
    if cel_expr is None or cel_expr.strip() in ("", "true"):
        return True
    try:
        return bool(_program(cel_expr).evaluate(activation))
    except Exception:  # noqa: BLE001 - any compile/eval failure fails safe (rule still counts)
        return True


async def _contributors(
    session: AsyncSession, org_id: UUID, action_type: str, activation: dict[str, Any]
) -> tuple[list[Contributor], list[str]]:
    """The matching contributors for one `action_type` (core tier-4 + pack/tenant rules + incident
    tightening) — *without* the empty-set fallback, so a caller can pool several actions and apply
    that fallback once (BLOCKERS #20)."""
    contributors: list[Contributor] = []
    matched: list[str] = []

    if action_type in CORE_TIER4_ACTIONS:
        contributors.append((NEVER_AUTONOMOUS_TIER, "core:tier4", [], None, "cancel", None))
        matched.append("core:tier4")

    rows = (
        await session.execute(
            text(
                "SELECT id, tier, cel_expr, approver_chain, timeout_s, on_timeout, confirm_kind "
                "FROM approval_policies WHERE action_type = :at"
            ),
            {"at": action_type},
        )
    ).mappings().all()
    for r in rows:
        if _matches(r["cel_expr"], activation):
            rid = str(r["id"])
            contributors.append((
                int(r["tier"]), rid, list(r["approver_chain"] or []),
                r["timeout_s"], r["on_timeout"], r["confirm_kind"],
            ))
            matched.append(rid)

    inc = (
        await session.execute(
            text(
                "SELECT tightened_to_tier FROM incident_tightening "
                "WHERE org_id = :o AND action_type = :at AND expires_at > now() "
                "ORDER BY tightened_to_tier DESC LIMIT 1"
            ),
            {"o": str(org_id), "at": action_type},
        )
    ).scalar_one_or_none()
    if inc is not None:
        contributors.append((int(inc), "incident", [], None, "hold", None))
        matched.append("incident")

    return contributors, matched


async def evaluate(session: AsyncSession, ctx: ActionContext) -> Decision:
    """Deterministic tier for `ctx`. Max tier wins; matched rule ids recorded; order-independent."""
    await repository.set_org_context(session, ctx.org_id)
    contributors, matched = await _contributors(
        session, ctx.org_id, ctx.action_type, _activation(ctx))
    return select_decision(contributors, matched)


# ---- tool → abstract-action bridge (BLOCKERS #20) ------------------------------------------------
# The mediation proxy calls tools by name (`messages.send`), but pack tier rules are keyed by the
# **abstract action** they govern (`action.message.send`). A tool call is evaluated against every
# abstract action it maps to; max tier wins. `messages.send` is *also* a quote-send when the message
# carries a price (structured `amount_minor`, or a money figure in the body — "a message with a
# price is a quote"), so the quote tiers (high-value / discount) then apply.
TOOL_ACTIONS: dict[str, list[str]] = {
    "messages.send": ["action.message.send"],
    "campaigns.execute": ["action.campaign.execute"],
    "catalog.write": ["action.catalog.write"],
}


def _message_amount_minor(params: dict[str, Any]) -> int | None:
    """The price a `messages.send` carries: the structured `amount_minor` if given, else the largest
    money figure parsed from the body. None → the message carries no price (a plain reply)."""
    amount = params.get("amount_minor")
    if amount:
        return int(amount)
    from core.pricing.extract import extract_amounts

    body = str(params.get("body") or params.get("text") or "")
    return max((f.minor for f in extract_amounts(body)), default=None)


def resolve_actions(tool: str, params: dict[str, Any]) -> list[str]:
    """The abstract action(s) a tool call governs. `messages.send` adds `action.quote.send` when the
    message carries a price. Falls back to the tool name when the tool has no mapping."""
    actions = list(TOOL_ACTIONS.get(tool, []))
    if tool == "messages.send" and _message_amount_minor(params) is not None:
        actions.append("action.quote.send")
    return actions or [tool]


async def evaluate_tool(
    session: AsyncSession, *, org_id: UUID, actor_instance_id: UUID | None, untrusted: bool,
    tool: str, params: dict[str, Any],
) -> Decision:
    """Evaluate a *tool* call against its abstract-action family and return the max-tier decision
    (BLOCKERS #20). `amount_minor` is populated from the message price so the quote-tier CEL
    (`amount_minor >= …`) evaluates; optional attributes (discount, sentiment) are passed through
    as-is and the pack rules `has()`-guard them, so an absent field means the condition is not met
    (rather than fail-safe-matching)."""
    await repository.set_org_context(session, org_id)
    amount = (
        _message_amount_minor(params) if tool == "messages.send" else params.get("amount_minor")
    )
    activation = _activation(ActionContext(
        org_id=org_id, action_type=tool, actor_instance_id=actor_instance_id,
        amount_minor=int(amount) if amount is not None else None,
        currency=params.get("currency"), recipients=list(params.get("recipients", [])),
        attributes=dict(params), untrusted_content=untrusted,
    ))
    # Pool contributors across the whole action family, then apply the empty-set fallback ONCE —
    # so a quote with no matching quote-rule falls back to the message tier, not to "unknown → 2".
    contributors: list[Contributor] = []
    matched: list[str] = []
    for action in resolve_actions(tool, params):
        c, m = await _contributors(session, org_id, action, activation)
        contributors.extend(c)
        matched.extend(m)
    return select_decision(contributors, matched)


# A contributor: (tier, stable_sort_key, approver_chain, timeout_s, on_timeout, confirm_kind).
Contributor = tuple[int, str, list[Any], int | None, str, str | None]


def select_decision(contributors: list[Contributor], matched: list[str]) -> Decision:
    """Max tier wins; ties broken by the stable sort key. Pure and **order-independent** — the
    same contributor set yields the same decision under any ordering (the determinism property)."""
    if not contributors:  # no rule at all — fail safe to needs-approval
        return Decision(tier=DEFAULT_UNKNOWN_TIER, matched_rules=[])
    winner = max(contributors, key=lambda c: (c[0], c[1]))
    return Decision(
        tier=winner[0], matched_rules=sorted(matched), approver_chain=winner[2],
        timeout_s=winner[3], on_timeout=winner[4], confirm_kind=winner[5],
    )


async def baseline_tier(session: AsyncSession, action_type: str) -> int:
    """The highest tier the core/pack layers require for `action_type` (a tenant may not go below).
    Context-independent: the max over all core/pack rules regardless of CEL guards (a tenant rule
    must not be able to loosen even the strictest baseline case). Global rows are visible under
    RLS regardless of org context (`org_id IS NULL` read policy)."""
    base = NEVER_AUTONOMOUS_TIER if action_type in CORE_TIER4_ACTIONS else 0
    rows = (
        await session.execute(
            text(
                "SELECT COALESCE(MAX(tier), 0) AS m FROM approval_policies "
                "WHERE action_type = :at AND scope IN ('core','pack')"
            ),
            {"at": action_type},
        )
    ).scalar_one()
    return max(base, int(rows))


async def validate_tenant_rule(session: AsyncSession, action_type: str, tier: int) -> None:
    """Tighten-only: a tenant rule may only raise the tier. Raises if it would loosen below the
    core/pack baseline."""
    base = await baseline_tier(session, action_type)
    if tier < base:
        raise GrowthOperatorError(
            "config_schema_violation",
            f"tenant rule tier {tier} lowers {action_type!r} below baseline {base} (tighten-only)",
        )
