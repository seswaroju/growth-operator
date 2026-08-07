"""Owner dashboard read-model (Phase 3, Ticket 3.1).

`dashboard_overview` returns the store-owner Home KPI counts for one org — pending approvals,
open conversations, active catalog items, and open support tickets. The counts are org-scoped two
ways (belt-and-suspenders, matching `core/approvals/service.list_approvals`): the service re-asserts
`app.org_id` from the *verified* caller org (RLS), and every subquery also filters `org_id`
explicitly — so a count can never span tenants.

Owner-facing **outcomes only**: this surface stays operational. The analytics/intelligence engine
(Phase 3.5) extends the owner Home with distilled campaign/ROI figures; the CEO-grade math lives in
the operator console (Phase 4). See `project-management/DECISIONS.md` (2026-08-06 analytics entry).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context


@dataclass(frozen=True)
class DashboardOverview:
    pending_approvals: int
    open_conversations: int
    catalog_items: int
    open_tickets: int


# One round-trip: four org-scoped COUNTs. `status` values match the tables' CHECK constraints
# (approvals: pending/approved/rejected/expired; catalog_items: active/archived;
# support_tickets: open/in_progress/resolved/closed; conversations default 'open').
_OVERVIEW_SQL = text(
    """
    SELECT
      (SELECT count(*) FROM approvals
         WHERE org_id = :o AND status = 'pending')                       AS pending_approvals,
      (SELECT count(*) FROM conversations
         WHERE org_id = :o AND status = 'open')                          AS open_conversations,
      (SELECT count(*) FROM catalog_items
         WHERE org_id = :o AND status = 'active')                        AS catalog_items,
      (SELECT count(*) FROM support_tickets
         WHERE org_id = :o AND status IN ('open', 'in_progress'))        AS open_tickets
    """
)


async def dashboard_overview(session: AsyncSession, org_id: UUID) -> DashboardOverview:
    """Home KPI counts for one org — scoped to the verified caller's tenant."""
    await set_org_context(session, org_id)
    row = (await session.execute(_OVERVIEW_SQL, {"o": str(org_id)})).mappings().one()
    return DashboardOverview(
        pending_approvals=int(row["pending_approvals"]),
        open_conversations=int(row["open_conversations"]),
        catalog_items=int(row["catalog_items"]),
        open_tickets=int(row["open_tickets"]),
    )
