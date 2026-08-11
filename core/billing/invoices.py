"""Monthly invoices / statements from recorded charges (OC12).

A store's monthly invoice is the aggregate of its `billing_charges` for a month — one immutable
statement per store per month, so the number is **deterministic** (`{STORE}-INV-{YYMM}`) with no
sequence to maintain. Generated on the fly from the existing charges (no new table). **Amount only**
— GO's internal `cost_minor`/margin is never on a client invoice. Org-scoped (`set_org_context`).
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import service as billing_service
from core.payments.transactions import store_code
from core.tenancy.repository import set_org_context

_SELLER = "Growth Operator"


def _ym(period_month: date) -> str:
    return f"{period_month.year % 100:02d}{period_month.month:02d}"


def _invoice_no(store_name: str, period_month: date) -> str:
    return f"{store_code(store_name)}-INV-{_ym(period_month)}"


async def _org_name(session: AsyncSession, org_id: UUID) -> str:
    name = (await session.execute(
        text("SELECT name FROM organizations WHERE id = :o"), {"o": str(org_id)})
    ).scalar_one_or_none()
    return str(name) if name else "Customer"


async def monthly_invoice(
    session: AsyncSession, org_id: UUID, period_month: date
) -> dict[str, Any]:
    """The full statement for one store-month: line items by channel (amount only) + total."""
    await set_org_context(session, org_id)
    lines = await billing_service.monthly_spend_by_channel(session, org_id, period_month)
    total = sum(int(row["amount_minor"]) for row in lines)
    name = await _org_name(session, org_id)
    return {
        "invoice_no": _invoice_no(name, period_month),
        "period_month": period_month.strftime("%Y-%m"),
        "seller_name": _SELLER, "buyer_name": name,
        "currency": "INR", "line_items": lines, "total_minor": total,
    }


async def list_invoices(session: AsyncSession, org_id: UUID) -> list[dict[str, Any]]:
    """One row per month that has charges: number + period + total, newest first."""
    await set_org_context(session, org_id)
    rows = (await session.execute(
        text("SELECT date_trunc('month', period_month)::date AS pm, "
             "COALESCE(SUM(amount_minor), 0) AS total "
             "FROM billing_charges WHERE org_id = :o "
             "GROUP BY 1 ORDER BY 1 DESC"),
        {"o": str(org_id)})).mappings().all()
    name = await _org_name(session, org_id)
    return [
        {"invoice_no": _invoice_no(name, r["pm"]), "period_month": r["pm"].strftime("%Y-%m"),
         "total_minor": int(r["total"])}
        for r in rows
    ]
