"""Simulated intelligence agents (Phase 3.5-eng, A4.4).

Two deterministic report producers — **competitor-analysis** (over the owner's tracked competitors)
and **marketing-strategist** (over the store's weekly metrics) — that write layered insight records
(`agent_reports`). They are **gated-simulated**: while `llm_provider_enabled` is off (default) they
produce deterministic, clearly-labelled output; when it is on but the real agent isn't wired they
**fail closed** (`provider_unavailable`) — the same posture as `RealModel`/embeddings. A real LLM +
live competitor-data source replace the simulated bodies at go-live (no interface change).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.common.config import get_settings
from core.common.errors import GrowthOperatorError
from core.competitors import service as competitors
from core.insights import metrics, reports

_SIM_NOTE = "Simulated analysis — a real model + live data replace this at go-live."


def _gate() -> None:
    """Fail closed if the real LLM is enabled but the agent isn't wired (RealModel posture)."""
    if get_settings().llm_provider_enabled:
        raise GrowthOperatorError(
            "provider_unavailable", "intelligence agent needs the real LLM (not wired)")


async def produce_competitor_report(session: AsyncSession, org_id: UUID) -> UUID:
    """A competitor-analysis insight over the owner's tracked competitors (simulated)."""
    _gate()
    rivals = await competitors.list_competitors(session, org_id)
    drivers: list[dict[str, Any]] = []
    for c in rivals:
        where = f" ({c['handle']})" if c.get("handle") else ""
        drivers.append({
            "label": c["name"],
            "detail": f"Tracked competitor{where} — watch their festival offers + pricing.",
            "sentiment": "neutral",
        })
    if not drivers:
        drivers.append({
            "label": "No competitors tracked",
            "detail": "Add competitors to watch so this analysis can compare you against them.",
            "sentiment": "neutral",
        })
    verdict = (f"Watching {len(rivals)} competitor(s). " if rivals
               else "No competitors tracked yet. ") + _SIM_NOTE
    return await reports.create_report(
        session, org_id, report_type="competitor_analysis", title="Competitive landscape",
        verdict=verdict, drivers=drivers,
        full_breakdown={"competitors": [c["name"] for c in rivals], "simulated": True},
        evidence=[], confidence="low", model="simulated",
    )


async def produce_marketing_report(session: AsyncSession, org_id: UUID) -> UUID:
    """A marketing-strategist insight over the store's weekly metrics (simulated heuristics)."""
    _gate()
    summary = {m.metric_key: m.this_week for m in await metrics.weekly_summary(session, org_id)}
    inquiries = summary.get("leads_created", 0)
    quotes = summary.get("quotes_sent", 0)
    orders = summary.get("orders", 0)
    drivers: list[dict[str, Any]] = []
    if inquiries and quotes < inquiries:
        drivers.append({
            "label": "Send more quotes",
            "detail": f"{inquiries} new inquiries but only {quotes} quotes — quote every "
                      "interested customer.", "sentiment": "bad"})
    if quotes and orders < quotes:
        drivers.append({
            "label": "Follow up",
            "detail": f"{quotes} quotes but {orders} sales — a follow-up nudge closes more.",
            "sentiment": "bad"})
    if not drivers:
        drivers.append({
            "label": "Keep it up",
            "detail": "Your inquiry → quote → sale flow looks healthy this week.",
            "sentiment": "good"})
    return await reports.create_report(
        session, org_id, report_type="marketing_strategy", title="Marketing recommendations",
        verdict=f"{inquiries} new inquiries this week. " + _SIM_NOTE, drivers=drivers,
        full_breakdown={"this_week": {"inquiries": inquiries, "quotes": quotes, "orders": orders},
                        "simulated": True},
        evidence=[], confidence="low", model="simulated",
    )
