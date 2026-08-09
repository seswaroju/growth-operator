"""Workflow guard library (MVP-072, docs/21-platform/workflow-engine.md).

Seven pure async predicates over L2/L3 state, referenced by name in a workflow's `guards:` list and
evaluated at trigger AND before every external-effect step (the executor wiring is MVP-073):

    consent_valid(purpose) · not_suppressed · within_send_window · touch_cap(n, window)
    budget_ok · flag_on(key) · tier_max(n)

**Fail-closed:** a guard that needs a subject it wasn't given (no contact in context) blocks rather
than guesses — no context, no run. `mandated_guards` a pack declares are injected server-side
(`inject_mandated_guards`) so a crafted definition can never ship without them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context
from core.workflows.schema import parse_duration_s

GUARD_NAMES: frozenset[str] = frozenset({
    "consent_valid", "not_suppressed", "within_send_window", "touch_cap",
    "budget_ok", "flag_on", "tier_max",
})

# Quiet-hours morning boundary until an explicit `quiet_hours.end` setting is registered. The window
# is [quiet_hours.start .. QUIET_END) local time; a send inside it is blocked by within_send_window.
QUIET_END = time(8, 0)
# Marketing consent requires the strongest signal; other purposes accept an implicit opt-in.
_CONSENT_OK_STRICT = frozenset({"explicit"})
_CONSENT_OK_LOOSE = frozenset({"explicit", "implicit"})


class UnknownGuard(ValueError):
    """A `guards:` entry names a guard outside the core library."""


@dataclass(frozen=True)
class GuardRef:
    name: str
    args: tuple[str, ...] = ()

    def render(self) -> str:
        return f"{self.name}({', '.join(self.args)})" if self.args else self.name


@dataclass
class GuardContext:
    """The L2/L3 slice a guard reads. `now` is tenant-local; ids are absent for non-contact runs."""
    org_id: UUID
    now: datetime
    contact_id: UUID | None = None
    lead_id: UUID | None = None
    vars: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GuardResult:
    passed: bool
    guard: str
    reason: str | None = None


_REF_RE = re.compile(r"^\s*([a-z_]+)\s*(?:\((?P<args>.*)\))?\s*$")


def parse_guard_ref(expr: str) -> GuardRef:
    """`"touch_cap(3, 30d)"` → GuardRef('touch_cap', ('3','30d')). Raises `UnknownGuard`."""
    m = _REF_RE.match(expr)
    if not m:
        raise UnknownGuard(f"malformed guard reference: {expr!r}")
    name = m.group(1)
    if name not in GUARD_NAMES:
        raise UnknownGuard(f"unknown guard: {name!r}")
    raw = m.group("args")
    args = tuple(a.strip() for a in raw.split(",")) if raw and raw.strip() else ()
    return GuardRef(name, args)


def inject_mandated_guards(
    declared: list[GuardRef], mandated: list[GuardRef]
) -> list[GuardRef]:
    """Return `declared` with every `mandated` guard whose NAME is absent appended (server-side).

    Idempotent and name-keyed: a definition that already lists `not_suppressed` is left as-is; one
    that omits a mandated `not_suppressed` gets it added, so a crafted request cannot drop it.
    """
    have = {g.name for g in declared}
    return list(declared) + [g for g in mandated if g.name not in have]


# ---- Predicates -----------------------------------------------------------------------


async def _not_suppressed(session: AsyncSession, ctx: GuardContext, args: tuple[str, ...]) -> bool:
    if ctx.contact_id is None:
        return False  # fail closed — cannot prove the contact is un-suppressed
    row = await session.execute(
        text("SELECT 1 FROM suppressions WHERE contact_id = :c AND scope IN ('marketing','all')"),
        {"c": str(ctx.contact_id)})
    return row.first() is None


async def _consent_valid(session: AsyncSession, ctx: GuardContext, args: tuple[str, ...]) -> bool:
    if ctx.contact_id is None:
        return False  # fail closed
    purpose = args[0] if args else "marketing"
    status = (await session.execute(
        text("SELECT consent_status FROM contacts WHERE id = :c"),
        {"c": str(ctx.contact_id)})).scalar_one_or_none()
    ok = _CONSENT_OK_STRICT if purpose == "marketing" else _CONSENT_OK_LOOSE
    return status in ok


async def _within_send_window(
    session: AsyncSession, ctx: GuardContext, args: tuple[str, ...]
) -> bool:
    from core.tenancy import settings as settings_mod
    start_raw = (await settings_mod.resolve(session, ctx.org_id, "quiet_hours.start")).value
    try:
        hh, mm = (int(x) for x in str(start_raw).split(":"))
        start = time(hh, mm)
    except (ValueError, TypeError):
        start = time(21, 0)
    now_t = ctx.now.time()
    # Quiet window wraps midnight (e.g. 21:00 → 08:00): inside if after start OR before the morning
    # boundary. A send is allowed only OUTSIDE that window.
    in_quiet = now_t >= start or now_t < QUIET_END
    return not in_quiet


async def _touch_cap(session: AsyncSession, ctx: GuardContext, args: tuple[str, ...]) -> bool:
    if ctx.contact_id is None:
        return False  # fail closed
    n = int(args[0]) if args else 1
    window_s = parse_duration_s(args[1]) if len(args) > 1 else 30 * 86400
    count = (await session.execute(
        text("SELECT count(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id "
             "WHERE c.contact_id = :c AND m.direction = 'outbound' "
             "AND m.created_at > now() - make_interval(secs => :w)"),
        {"c": str(ctx.contact_id), "w": window_s})).scalar_one()
    return int(count) < n


async def _budget_ok(session: AsyncSession, ctx: GuardContext, args: tuple[str, ...]) -> bool:
    """Pass unless a managed budget cap is provided in context AND this month's spend exceeds it.

    No stored per-tenant cap exists yet (billing budgets are a later ticket), so with no
    `budget_cap_minor` in context the guard fails OPEN — you cannot exceed a budget you never set.
    When a cap is supplied it bites immediately against summed `billing_charges` for the month.
    """
    cap = ctx.vars.get("budget_cap_minor")
    if cap is None:
        return True
    spent = (await session.execute(
        text("SELECT COALESCE(sum(amount_minor), 0) FROM billing_charges "
             "WHERE period_month = date_trunc('month', current_date)::date"))).scalar_one()
    return int(spent) < int(cap)


async def _flag_on(session: AsyncSession, ctx: GuardContext, args: tuple[str, ...]) -> bool:
    if not args:
        return False
    key = args[0]
    # Tenant override wins over the flag's default; an undefined flag fails closed (not on).
    value = (await session.execute(
        text("SELECT COALESCE("
             "  (SELECT r.value FROM flag_rules r JOIN feature_flags f ON f.id = r.flag_id "
             "   WHERE f.key = :k AND r.scope = 'tenant' AND r.scope_ref = :o "
             "   ORDER BY r.precedence LIMIT 1), "
             "  (SELECT default_value FROM feature_flags WHERE key = :k))"),
        {"k": key, "o": str(ctx.org_id)})).scalar_one_or_none()
    return bool(value)


async def _tier_max(session: AsyncSession, ctx: GuardContext, args: tuple[str, ...]) -> bool:
    """A declared autonomy ceiling, not a trigger predicate: it never blocks run creation. The cap
    is enforced per external-effect step by the approval engine (MVP-073), so here it passes."""
    return True


_GUARD_FUNCS = {
    "not_suppressed": _not_suppressed,
    "consent_valid": _consent_valid,
    "within_send_window": _within_send_window,
    "touch_cap": _touch_cap,
    "budget_ok": _budget_ok,
    "flag_on": _flag_on,
    "tier_max": _tier_max,
}


async def evaluate_guard(
    session: AsyncSession, ref: GuardRef, ctx: GuardContext
) -> GuardResult:
    """Evaluate one guard against `ctx`. Sets tenant context so RLS-scoped reads see rows."""
    if ref.name not in _GUARD_FUNCS:
        raise UnknownGuard(f"unknown guard: {ref.name!r}")
    await set_org_context(session, ctx.org_id)
    passed = await _GUARD_FUNCS[ref.name](session, ctx, ref.args)
    return GuardResult(passed=passed, guard=ref.render(),
                       reason=None if passed else f"guard {ref.render()} blocked")


async def evaluate_all(
    session: AsyncSession, refs: list[GuardRef], ctx: GuardContext
) -> list[GuardResult]:
    """Evaluate every guard; the caller treats any `passed=False` as a block (run not created)."""
    return [await evaluate_guard(session, r, ctx) for r in refs]


def first_block(results: list[GuardResult]) -> GuardResult | None:
    """First failing guard, or None if all passed — the `workflow.skipped` reason (MVP-073)."""
    return next((r for r in results if not r.passed), None)
