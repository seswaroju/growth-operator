"""Business-metrics computation + read model (Phase 3.5-eng, Ticket A1).

The daily rollup (`core.insights.rollup`) computes these per org from the domain tables and upserts
them into `business_metrics`; the read functions serve the owner dashboard's outcome cards. Money
metrics live in `value_minor` (integer minor units); counts live in `value_numeric`. Org-scoped
(RLS via `set_org_context` + explicit `org_id` filter).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context

# The metric keys the rollup materialises. Money keys are stored in value_minor.
METRIC_KEYS: tuple[str, ...] = (
    "leads_created", "quotes_sent", "orders", "revenue_minor", "messages_in", "messages_out",
)
MONEY_KEYS: frozenset[str] = frozenset({"revenue_minor"})


async def compute_day(session: AsyncSession, org_id: UUID, day: date) -> dict[str, int]:
    """The metric values for one org + calendar day, from the domain tables."""
    await set_org_context(session, org_id)
    row = (
        await session.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM leads
                     WHERE org_id = :o AND created_at::date = :d) AS leads_created,
                  (SELECT count(*) FROM quotes
                     WHERE org_id = :o AND created_at::date = :d) AS quotes_sent,
                  (SELECT count(*) FROM orders
                     WHERE org_id = :o AND created_at::date = :d) AS orders,
                  (SELECT COALESCE(SUM(total_minor), 0) FROM orders
                     WHERE org_id = :o AND created_at::date = :d) AS revenue_minor,
                  (SELECT count(*) FROM messages
                     WHERE org_id = :o AND direction = 'inbound'
                       AND created_at::date = :d) AS messages_in,
                  (SELECT count(*) FROM messages
                     WHERE org_id = :o AND direction = 'outbound'
                       AND created_at::date = :d) AS messages_out
                """
            ),
            {"o": str(org_id), "d": day},
        )
    ).mappings().one()
    return {k: int(row[k]) for k in METRIC_KEYS}


async def upsert_day(
    session: AsyncSession, org_id: UUID, day: date, values: dict[str, int]
) -> None:
    """Idempotently upsert one day's metrics (re-running a day overwrites)."""
    await set_org_context(session, org_id)
    for key, val in values.items():
        money = key in MONEY_KEYS
        await session.execute(
            text(
                """
                INSERT INTO business_metrics
                  (org_id, metric_date, metric_key, value_numeric, value_minor, computed_at)
                VALUES (:o, :d, :k, :vn, :vm, now())
                ON CONFLICT (org_id, metric_date, metric_key, dimension)
                DO UPDATE SET value_numeric = EXCLUDED.value_numeric,
                              value_minor = EXCLUDED.value_minor, computed_at = now()
                """
            ),
            {"o": str(org_id), "d": day, "k": key,
             "vn": 0 if money else val, "vm": val if money else None},
        )


@dataclass(frozen=True)
class MetricSummary:
    metric_key: str
    this_week: int
    last_week: int
    delta_pct: float | None  # None when last_week == 0 (no baseline)


async def weekly_summary(
    session: AsyncSession, org_id: UUID, today: date | None = None
) -> list[MetricSummary]:
    """Per-metric this-week vs last-week totals + % change, from `business_metrics`.

    This week = the last 7 days (incl. today); last week = the 7 days before that.
    """
    await set_org_context(session, org_id)
    today = today or date.today()
    this_start = today - timedelta(days=6)
    last_start = today - timedelta(days=13)
    last_end = today - timedelta(days=7)
    rows = (
        await session.execute(
            text(
                """
                SELECT metric_key,
                  COALESCE(SUM(CASE WHEN metric_date >= :ts
                    THEN COALESCE(value_minor, value_numeric) END), 0) AS this_week,
                  COALESCE(SUM(CASE WHEN metric_date >= :ls AND metric_date <= :le
                    THEN COALESCE(value_minor, value_numeric) END), 0) AS last_week
                FROM business_metrics
                WHERE org_id = :o AND metric_date >= :ls
                GROUP BY metric_key
                """
            ),
            {"o": str(org_id), "ts": this_start, "ls": last_start, "le": last_end},
        )
    ).mappings().all()
    by_key = {r["metric_key"]: (int(r["this_week"]), int(r["last_week"])) for r in rows}
    out: list[MetricSummary] = []
    for key in METRIC_KEYS:
        this_week, last_week = by_key.get(key, (0, 0))
        delta = ((this_week - last_week) / last_week * 100) if last_week else None
        out.append(MetricSummary(key, this_week, last_week,
                                 round(delta, 1) if delta is not None else None))
    return out
