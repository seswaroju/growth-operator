"""Per-store cost & margin, itemised (CP-6).

The operator's "what am I making on this store" view. For one store + one month it folds two sources
into one itemised breakdown:

  - **`billing_charges`** — the revenue GO bills (`amount_minor`) and GO's cost (`cost_minor`) per
    charge type. Platform APIs (whatsapp / instagram / google_ads) are their own lines, **separate
    from the plan**; add-ons (social / seo / campaign) too.
  - **`costs_lite`** — the LLM provider spend (`cost_usd`) the runtime logs per turn. LLM is the
    exception: it's **baked into the plan** (a cost against the subscription, not billed
    separately), so it shows as a line with revenue 0 and a pure cost. USD is converted to INR
    paise at the configured `usd_inr_rate`.

Everything nets to a single `margin = revenue − cost`. Both source tables are org-scoped (RLS); the
caller reads them under the target org's tenant context.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.config import get_settings
from core.tenancy import repository

# charge_type → display label. `subscription` + `llm` are always shown; the rest appear when used.
_LABELS: dict[str, str] = {
    "subscription": "Subscription (plan)",
    "whatsapp": "WhatsApp API",
    "instagram": "Instagram API",
    "google_ads": "Google Ads",
    "social": "Social",
    "seo": "SEO",
    "campaign": "Campaigns",
    "other": "Other",
}
# Line order: the plan + its in-plan LLM cost, then the separately-billed platform APIs, then add-on
# services.
_ORDER = ("subscription", "llm", "whatsapp", "instagram", "google_ads", "social", "seo",
          "campaign", "other")
_ALWAYS = frozenset({"subscription"})  # shown even at zero (llm is always appended explicitly)


@dataclass(frozen=True)
class CostMarginLine:
    category: str
    label: str
    revenue_minor: int
    cost_minor: int
    margin_minor: int


@dataclass(frozen=True)
class LlmDetail:
    cost_usd: str  # native provider spend (what costs_lite recorded)
    cost_minor: int  # converted to INR paise at usd_inr_rate
    runs: int
    tokens_in: int
    tokens_out: int


@dataclass(frozen=True)
class CostMargin:
    month: str  # YYYY-MM
    currency: str
    usd_inr_rate: float
    lines: list[CostMarginLine]
    llm: LlmDetail
    revenue_minor: int
    cost_minor: int
    margin_minor: int


def usd_to_minor(cost_usd: Decimal, rate: float) -> int:
    """USD amount → INR paise at `rate` (rounded to the paisa)."""
    return int((cost_usd * Decimal(str(rate)) * 100).quantize(Decimal("1")))


async def cost_margin_for_month(
    session: AsyncSession, org_id: UUID, month: date
) -> CostMargin:
    """Itemised cost + margin for one store for the month containing `month`."""
    await repository.set_org_context(session, org_id)
    month_start = month.replace(day=1)
    # first of next month (day 28 + 4 days always lands in the next month)
    month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)

    # 1. Recorded charges (revenue + GO cost) grouped by type, for the month.
    rows = (
        await session.execute(
            text("SELECT charge_type, COALESCE(SUM(amount_minor),0) rev, "
                 "COALESCE(SUM(cost_minor),0) cost FROM billing_charges "
                 "WHERE period_month = :m GROUP BY charge_type"),
            {"m": month_start})
    ).mappings().all()
    charges = {r["charge_type"]: (int(r["rev"]), int(r["cost"])) for r in rows}

    # 2. LLM spend (costs_lite) for the month → INR. LLM is in-plan, so revenue is 0.
    llm_row = (
        await session.execute(
            text("SELECT COALESCE(SUM(cost_usd),0) usd, COUNT(*) runs, "
                 "COALESCE(SUM(tokens_in),0) tin, COALESCE(SUM(tokens_out),0) tout "
                 "FROM costs_lite WHERE created_at >= :s AND created_at < :e"),
            {"s": month_start, "e": month_end})
    ).mappings().one()
    rate = get_settings().usd_inr_rate
    llm_usd = Decimal(str(llm_row["usd"]))
    llm_minor = usd_to_minor(llm_usd, rate)  # from full precision
    llm = LlmDetail(
        cost_usd=str(llm_usd.quantize(Decimal("0.0001"))),  # 4dp for a clean display
        cost_minor=llm_minor, runs=int(llm_row["runs"]),
        tokens_in=int(llm_row["tin"]), tokens_out=int(llm_row["tout"]))

    # 3. Ordered, itemised lines.
    lines: list[CostMarginLine] = []
    for cat in _ORDER:
        if cat == "llm":
            lines.append(CostMarginLine("llm", "LLM (in plan)", 0, llm_minor, -llm_minor))
            continue
        rev, cost = charges.get(cat, (0, 0))
        if rev == 0 and cost == 0 and cat not in _ALWAYS:
            continue  # hide empty API/add-on lines; subscription + llm always show
        lines.append(CostMarginLine(cat, _LABELS[cat], rev, cost, rev - cost))

    total_rev = sum(ln.revenue_minor for ln in lines)
    total_cost = sum(ln.cost_minor for ln in lines)
    return CostMargin(
        month=month_start.strftime("%Y-%m"), currency="INR", usd_inr_rate=rate,
        lines=lines, llm=llm,
        revenue_minor=total_rev, cost_minor=total_cost, margin_minor=total_rev - total_cost)
