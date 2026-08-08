"""Campaign-analysis producer (Phase 3.5-eng, A4.2).

Runs the deterministic campaign analytics engine (A2/A3) for a campaign and stores the result as a
layered insight record (`agent_reports`, report_type=`campaign_analysis`). **No LLM** — this is the
numeric analysis; the LLM-simulated competitor/marketing agents are A4.4.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.campaigns import analytics, attribution, service
from core.insights import reports


def _confidence(reached: int, is_significant: bool) -> str:
    if reached < analytics.MIN_SAMPLE:
        return "low"
    return "high" if is_significant and reached >= 100 else "medium"


async def produce_campaign_report(
    session: AsyncSession, org_id: UUID, campaign_id: UUID
) -> UUID:
    """Analyse a campaign and store the layered insight record. Returns the report id."""
    a = await attribution.campaign_analytics(session, org_id, campaign_id)
    camp = await service.get_campaign(session, org_id, campaign_id)
    name = camp["name"] if camp else "Campaign"
    breakdown = {
        "funnel": {"reached": a.reached, "leads": a.leads, "quotes": a.quotes, "sales": a.sales},
        "revenue_minor": a.revenue_minor,
        "cost_minor": a.cost_minor,
        "roi": {"roas": a.roi.roas, "roi_pct": a.roi.roi_pct, "net_minor": a.roi.net_minor},
        "significance": {
            "campaign_rate": a.significance.campaign_rate,
            "baseline_rate": a.significance.baseline_rate,
            "z": a.significance.z, "p_value": a.significance.p_value,
            "is_significant": a.significance.is_significant, "lift_pct": a.significance.lift_pct,
        },
        "drop_off": a.drop_off,
        "window_days": a.window_days,
    }
    return await reports.create_report(
        session, org_id,
        report_type="campaign_analysis",
        title=f"{name} — campaign analysis",
        verdict=analytics.verdict_line(a.headline, a.significance, a.roi),
        drivers=[
            {"label": d.label, "detail": d.detail, "sentiment": d.sentiment} for d in a.drivers
        ],
        full_breakdown=breakdown,
        evidence=[],
        confidence=_confidence(a.reached, a.significance.is_significant),
        model="deterministic",
        subject_ref=campaign_id,
    )
