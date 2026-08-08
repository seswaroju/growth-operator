"""Daily business-metrics rollup job (Phase 3.5-eng, Ticket A1).

Fans out over every org (one `org_scoped_session` per org — never a cross-tenant transaction) and
recomputes a trailing window of daily metrics into `business_metrics`. Idempotent: re-running a day
overwrites (the UNIQUE + upsert), so a missed run or late-arriving data self-heals on the next tick.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common import db as dbmod
from core.events import scheduler as sched
from core.insights import metrics
from core.tenancy.middleware import org_scoped_session

ROLLUP_WINDOW_DAYS = 30  # trailing month — covers the week-over-week summary + a 30-day trend


async def rollup_org(
    session: AsyncSession, org_id: UUID, *, today: date | None = None,
    window: int = ROLLUP_WINDOW_DAYS,
) -> None:
    """Recompute the trailing-window daily metrics for one org (idempotent upsert)."""
    today = today or date.today()
    for n in range(window):
        day = today - timedelta(days=n)
        values = await metrics.compute_day(session, org_id, day)
        await metrics.upsert_day(session, org_id, day, values)


async def run_metrics_rollup() -> None:
    """Recompute the trailing-window daily metrics for every org (one txn per org)."""
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        org_ids = (await s.execute(text("SELECT id FROM organizations"))).scalars().all()
    for org_id in org_ids:
        async with org_scoped_session(org_id) as s:  # commits on clean exit
            await rollup_org(s, org_id)


def register_jobs() -> None:
    """Register the daily rollup (00:15 UTC)."""
    sched.register("business_metrics_rollup", "15 0 * * *", run_metrics_rollup)
