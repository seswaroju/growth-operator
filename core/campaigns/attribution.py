"""Campaign attribution + funnel (Phase 3.5-eng, A2.2+A3.1).

**Exact first-touch attribution**: a conversion (lead/quote/order) is credited to the campaign that
FIRST touched that contact within the attribution window before the conversion — deterministic and
auditable (no estimation). `campaign_funnel` builds the reached→leads→quotes→sales funnel + revenue;
`campaign_analytics` adds the significance test + drop-off on top. Org-scoped.

Multi-touch credit-splitting (the ambiguous case) is deferred (PRODUCTION_DEPTH_BACKLOG.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.campaigns import analytics
from core.tenancy.repository import set_org_context

DEFAULT_WINDOW_DAYS = 30


async def record_touch(
    session: AsyncSession, org_id: UUID, campaign_id: UUID, contact_id: UUID,
    occurred_at: datetime | None = None,
) -> None:
    """Record that `campaign_id` reached `contact_id` (the touch side of attribution)."""
    await set_org_context(session, org_id)
    await session.execute(
        text(
            "INSERT INTO campaign_touches (org_id, campaign_id, contact_id, occurred_at) "
            "VALUES (:o, :c, :ct, COALESCE(:at, now()))"
        ),
        {"o": str(org_id), "c": str(campaign_id), "ct": str(contact_id), "at": occurred_at},
    )


def _ft_eq(contact_col: str, ts_col: str) -> str:
    """SQL predicate: :c is the contact's first campaign touch within :w days before the event."""
    return (
        "(SELECT ct.campaign_id FROM campaign_touches ct "
        f" WHERE ct.org_id = :o AND ct.contact_id = {contact_col} "
        f"   AND ct.occurred_at <= {ts_col} "
        f"   AND ct.occurred_at >= {ts_col} - make_interval(days => :w) "
        " ORDER BY ct.occurred_at ASC LIMIT 1) = CAST(:c AS uuid)"
    )


async def campaign_funnel(
    session: AsyncSession, org_id: UUID, campaign_id: UUID, window_days: int = DEFAULT_WINDOW_DAYS
) -> dict[str, int]:
    """reached → leads → quotes → sales (+ revenue), each first-touch-attributed to the campaign."""
    await set_org_context(session, org_id)
    p = {"o": str(org_id), "c": str(campaign_id), "w": window_days}
    reached = (
        await session.execute(
            text("SELECT count(DISTINCT contact_id) FROM campaign_touches "
                 "WHERE org_id = :o AND campaign_id = CAST(:c AS uuid)"),
            p,
        )
    ).scalar_one()
    leads = (
        await session.execute(
            text(f"SELECT count(*) FROM leads l WHERE l.org_id = :o "
                 f"AND {_ft_eq('l.contact_id', 'l.created_at')}"),
            p,
        )
    ).scalar_one()
    quotes = (
        await session.execute(
            text("SELECT count(*) FROM quotes q JOIN leads l ON l.id = q.lead_id "
                 f"WHERE q.org_id = :o AND {_ft_eq('l.contact_id', 'q.created_at')}"),
            p,
        )
    ).scalar_one()
    sales = (
        await session.execute(
            text("SELECT count(*) AS sales, COALESCE(SUM(o.total_minor), 0) AS revenue "
                 "FROM orders o "
                 f"WHERE o.org_id = :o AND {_ft_eq('o.contact_id', 'o.created_at')}"),
            p,
        )
    ).mappings().one()
    return {"reached": int(reached), "leads": int(leads), "quotes": int(quotes),
            "sales": int(sales["sales"]), "revenue_minor": int(sales["revenue"])}


async def org_baseline_rate(session: AsyncSession, org_id: UUID) -> float:
    """The store's baseline contact→order rate (the null hypothesis for the significance test)."""
    await set_org_context(session, org_id)
    row = (
        await session.execute(
            text("SELECT (SELECT count(*) FROM contacts WHERE org_id = :o) AS contacts, "
                 "(SELECT count(DISTINCT contact_id) FROM orders WHERE org_id = :o) AS buyers"),
            {"o": str(org_id)},
        )
    ).mappings().one()
    contacts = int(row["contacts"])
    return (int(row["buyers"]) / contacts) if contacts else 0.0


@dataclass(frozen=True)
class CampaignAnalytics:
    campaign_id: UUID
    window_days: int
    reached: int
    leads: int
    quotes: int
    sales: int
    revenue_minor: int
    cost_minor: int
    roi: analytics.Roi
    significance: analytics.Significance
    drop_off: str | None
    headline: str
    drivers: list[analytics.Driver]


async def _campaign_cost(session: AsyncSession, org_id: UUID, campaign_id: UUID) -> int:
    """sent_count (from the execution record) × the owner's configured per-message cost. Both are
    real, org-scoped source values — no user-supplied figure enters the cost."""
    from core.tenancy import settings as tenant_settings

    sent = (
        await session.execute(
            text("SELECT sent_count FROM campaigns WHERE id = CAST(:c AS uuid) AND org_id = :o"),
            {"c": str(campaign_id), "o": str(org_id)},
        )
    ).scalar_one_or_none() or 0
    resolved = await tenant_settings.resolve(session, org_id, "campaign.cost_per_message_minor")
    return int(sent) * int(resolved.value or 0)


async def campaign_analytics(
    session: AsyncSession, org_id: UUID, campaign_id: UUID, window_days: int = DEFAULT_WINDOW_DAYS
) -> CampaignAnalytics:
    """The full "did it work + why": funnel + attributed revenue + ROI + significance + drop-off +
    the drivers. Revenue is only ever immutable order totals; ROI is deterministic + auditable."""
    funnel = await campaign_funnel(session, org_id, campaign_id, window_days)
    baseline = await org_baseline_rate(session, org_id)
    sig = analytics.significance(funnel["sales"], funnel["reached"], baseline)
    stages = analytics.funnel_stages([
        ("reached", funnel["reached"]), ("leads", funnel["leads"]),
        ("quotes", funnel["quotes"]), ("sales", funnel["sales"]),
    ])
    drop = analytics.drop_off(stages)
    cost = await _campaign_cost(session, org_id, campaign_id)
    roi_result = analytics.roi(funnel["revenue_minor"], cost)
    return CampaignAnalytics(
        campaign_id=campaign_id, window_days=window_days, reached=funnel["reached"],
        leads=funnel["leads"], quotes=funnel["quotes"], sales=funnel["sales"],
        revenue_minor=funnel["revenue_minor"], cost_minor=cost, roi=roi_result, significance=sig,
        drop_off=drop, headline=analytics.headline(sig, funnel["reached"]),
        drivers=analytics.drivers(funnel["reached"], sig, drop, roi_result),
    )
