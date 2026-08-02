"""Committed figures ledger (MVP-053).

Every committable figure the engine produces (the quote total + each visible breakdown line) is
recorded here with an expiry. The send-path check (MVP-054) then verifies that any monetary
amount about to go to a customer **matches an unexpired ledger row exactly** — tolerance is zero
minor units, so a paraphrased or off-by-one figure fails closed. Non-monetary commitments
(delivery windows etc.) are stored as `value_text` for warn-mode checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy import repository

DEFAULT_MATCH_WINDOW_HOURS = 48


@dataclass
class Figure:
    figure_type: str
    amount_minor: int | None = None
    value_text: str | None = None


async def write(
    session: AsyncSession, org_id: UUID, figures: list[Figure], *,
    source_ref: UUID, expires_at: datetime | None = None,
) -> int:
    """Record figures against a source (quote/order/booking). Returns the count written."""
    await repository.set_org_context(session, org_id)
    for f in figures:
        await session.execute(
            text(
                "INSERT INTO committed_figures_ledger "
                "(org_id, figure_type, amount_minor, value_text, source_ref, expires_at) "
                "VALUES (:org, :ft, :amt, :vt, :src, :exp)"
            ),
            {"org": str(org_id), "ft": f.figure_type, "amt": f.amount_minor,
             "vt": f.value_text, "src": str(source_ref), "exp": expires_at},
        )
    return len(figures)


async def match(
    session: AsyncSession, org_id: UUID, amount_minor: int, *,
    window_hours: int = DEFAULT_MATCH_WINDOW_HOURS,
) -> bool:
    """True iff an **unexpired** ledger row has exactly `amount_minor` (tolerance 0) within the
    match window. RLS scopes to the caller's org."""
    await repository.set_org_context(session, org_id)
    row = (
        await session.execute(
            text(
                "SELECT 1 FROM committed_figures_ledger "
                "WHERE org_id = :org AND amount_minor = :amt "
                "AND (expires_at IS NULL OR expires_at > now()) "
                "AND created_at > now() - make_interval(hours => :win) LIMIT 1"
            ),
            {"org": str(org_id), "amt": amount_minor, "win": window_hours},
        )
    ).first()
    return row is not None


def figures_from_breakdown(breakdown: list[dict[str, Any]], total_minor: int) -> list[Figure]:
    """The matchable figures for a quote: the total plus each positive breakdown line amount."""
    figures = [Figure(figure_type="total", amount_minor=total_minor)]
    for line in breakdown:
        amount = line.get("amount_minor")
        if isinstance(amount, int) and amount > 0 and line.get("id") != "total":
            figures.append(Figure(figure_type=line["id"], amount_minor=amount))
    return figures
