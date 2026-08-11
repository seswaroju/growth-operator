"""Per-channel budgets & caps (OC7).

A store can carry a **monthly budget per channel** (matching `billing_charges.charge_type`). Spend
is measured **month-to-date** against the current month's charges. When a budget's `enforce` flag is
on, a charge that would push the channel over its cap is **blocked** with the canonical
`budget_exceeded` error (§13); otherwise it's **alert-only** — allowed, but flagged `over` in the
status view so an operator can see it. Org-scoped (RLS via `set_org_context`); minor-unit amounts.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.errors import GrowthOperatorError
from core.tenancy.repository import set_org_context

_COLS = "id, org_id, charge_type, budget_minor, enforce, created_at, updated_at"


async def set_budget(
    session: AsyncSession, org_id: UUID, *, charge_type: str, budget_minor: int, enforce: bool,
) -> dict[str, Any]:
    """Create or update the monthly budget for one channel (one row per org+channel)."""
    await set_org_context(session, org_id)
    row = (await session.execute(
        text("INSERT INTO channel_budgets (org_id, charge_type, budget_minor, enforce) "
             "VALUES (:o, :ct, :b, :e) "
             "ON CONFLICT (org_id, charge_type) DO UPDATE "
             "SET budget_minor = :b, enforce = :e, updated_at = now() "
             f"RETURNING {_COLS}"),
        {"o": str(org_id), "ct": charge_type, "b": budget_minor, "e": enforce})).mappings().one()
    return dict(row)


async def delete_budget(session: AsyncSession, org_id: UUID, charge_type: str) -> bool:
    """Remove a channel's budget. Returns True if a row was deleted."""
    await set_org_context(session, org_id)
    row = (await session.execute(
        text("DELETE FROM channel_budgets WHERE org_id = :o AND charge_type = :ct RETURNING id"),
        {"o": str(org_id), "ct": charge_type})).first()
    return row is not None


async def _mtd_spend(session: AsyncSession, org_id: UUID, charge_type: str, on: date) -> int:
    """This-month spend (amount_minor) on a channel, for the month containing `on`."""
    total = (await session.execute(
        text("SELECT COALESCE(SUM(amount_minor), 0) FROM billing_charges "
             "WHERE org_id = :o AND charge_type = :ct "
             "AND date_trunc('month', period_month) = date_trunc('month', CAST(:d AS date))"),
        {"o": str(org_id), "ct": charge_type, "d": on})).scalar_one()
    return int(total)


def _status_row(charge_type: str, budget_minor: int, enforce: bool, spent_minor: int
                ) -> dict[str, Any]:
    remaining = budget_minor - spent_minor
    pct = round(spent_minor / budget_minor * 100, 1) if budget_minor > 0 else None
    return {
        "charge_type": charge_type, "budget_minor": budget_minor, "enforce": enforce,
        "spent_minor": spent_minor, "remaining_minor": remaining,
        "pct": pct, "over": spent_minor > budget_minor,
    }


async def budget_status(
    session: AsyncSession, org_id: UUID, on: date | None = None
) -> list[dict[str, Any]]:
    """Each budgeted channel with its month-to-date spend, remaining, %, and over flag."""
    await set_org_context(session, org_id)
    day = on or date.today()
    rows = (await session.execute(
        text(f"SELECT {_COLS} FROM channel_budgets WHERE org_id = :o ORDER BY charge_type"),
        {"o": str(org_id)})).mappings().all()
    out: list[dict[str, Any]] = []
    for b in rows:
        spent = await _mtd_spend(session, org_id, b["charge_type"], day)
        out.append(_status_row(b["charge_type"], int(b["budget_minor"]), b["enforce"], spent))
    return out


async def check_and_enforce(
    session: AsyncSession, org_id: UUID, charge_type: str, additional_minor: int,
    on: date | None = None,
) -> dict[str, Any] | None:
    """Before recording a charge: if the channel has a budget and this would exceed it, raise
    `budget_exceeded` when `enforce` is on. Returns the resulting status (with `over`), or None when
    the channel has no budget."""
    await set_org_context(session, org_id)
    day = on or date.today()
    budget = (await session.execute(
        text("SELECT budget_minor, enforce FROM channel_budgets "
             "WHERE org_id = :o AND charge_type = :ct"),
        {"o": str(org_id), "ct": charge_type})).mappings().first()
    if budget is None:
        return None
    spent = await _mtd_spend(session, org_id, charge_type, day)
    projected = spent + int(additional_minor)
    if budget["enforce"] and projected > int(budget["budget_minor"]):
        raise GrowthOperatorError(
            "budget_exceeded",
            f"{charge_type} budget of {budget['budget_minor']} exceeded "
            f"(spent {spent} + {additional_minor})")
    return _status_row(charge_type, int(budget["budget_minor"]), budget["enforce"], projected)
