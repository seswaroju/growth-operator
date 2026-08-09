"""Billing service (B1) — OPERATOR-owned per-client revenue records.

Plans are a global GO catalog; subscriptions + charges are org-scoped (RLS). The operator writes a
client's subscription/charge by scoping the session to that target org (``set_org_context``) — a
normal scoped write, no `app.platform_admin` flag involved. The cross-client aggregate for the
dashboard comes from the ``platform_billing_rollup()`` SECURITY DEFINER function (sums only).
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context

_PLAN_COLS = "id, name, price_minor, active, created_at"
_CHARGE_COLS = "id, org_id, period_month, charge_type, amount_minor, cost_minor, note, created_at"


# ---- Plans (global GO catalog) -----------------------------------------------------------------

async def create_plan(session: AsyncSession, *, name: str, price_minor: int) -> dict[str, Any]:
    row = (await session.execute(
        text("INSERT INTO billing_plans (name, price_minor) VALUES (:n, :p) "
             f"RETURNING {_PLAN_COLS}"),
        {"n": name, "p": price_minor})).mappings().one()
    return dict(row)


async def list_plans(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (await session.execute(
        text(f"SELECT {_PLAN_COLS} FROM billing_plans ORDER BY price_minor"))).mappings().all()
    return [dict(r) for r in rows]


# ---- Subscriptions (one active plan per client) ------------------------------------------------

async def assign_subscription(
    session: AsyncSession, org_id: UUID, plan_id: UUID
) -> None:
    """Put the client on a plan: cancel any current active subscription, then start the new one."""
    await set_org_context(session, org_id)
    await session.execute(
        text("UPDATE billing_subscriptions SET status = 'cancelled', cancelled_at = now() "
             "WHERE org_id = :o AND status = 'active'"), {"o": str(org_id)})
    await session.execute(
        text("INSERT INTO billing_subscriptions (org_id, plan_id) VALUES (:o, :p)"),
        {"o": str(org_id), "p": str(plan_id)})


async def cancel_subscription(session: AsyncSession, org_id: UUID) -> None:
    await set_org_context(session, org_id)
    await session.execute(
        text("UPDATE billing_subscriptions SET status = 'cancelled', cancelled_at = now() "
             "WHERE org_id = :o AND status = 'active'"), {"o": str(org_id)})


async def get_subscription(session: AsyncSession, org_id: UUID) -> dict[str, Any] | None:
    await set_org_context(session, org_id)
    row = (await session.execute(
        text("SELECT s.id, s.plan_id, p.name AS plan_name, p.price_minor, s.status, s.started_at "
             "FROM billing_subscriptions s JOIN billing_plans p ON p.id = s.plan_id "
             "WHERE s.org_id = :o AND s.status = 'active'"), {"o": str(org_id)})).mappings().first()
    return dict(row) if row else None


# ---- Charges (per-client service line items: amount client pays + cost we pay) -----------------

async def record_charge(
    session: AsyncSession, org_id: UUID, *, period_month: date, charge_type: str,
    amount_minor: int, cost_minor: int, note: str | None, created_by: UUID | None,
) -> dict[str, Any]:
    await set_org_context(session, org_id)
    row = (await session.execute(
        text("INSERT INTO billing_charges "
             "(org_id, period_month, charge_type, amount_minor, cost_minor, note, created_by) "
             "VALUES (:o, date_trunc('month', CAST(:pm AS date))::date, :ct, :a, :c, :n, :by) "
             f"RETURNING {_CHARGE_COLS}"),
        {"o": str(org_id), "pm": period_month, "ct": charge_type, "a": amount_minor,
         "c": cost_minor, "n": note, "by": str(created_by) if created_by else None})
    ).mappings().one()
    return dict(row)


async def list_charges(session: AsyncSession, org_id: UUID) -> list[dict[str, Any]]:
    await set_org_context(session, org_id)
    rows = (await session.execute(
        text(f"SELECT {_CHARGE_COLS} FROM billing_charges WHERE org_id = :o "
             "ORDER BY period_month DESC, created_at DESC"), {"o": str(org_id)})).mappings().all()
    return [dict(r) for r in rows]


# ---- Cross-client aggregate for the Financial dashboard (SECDEF) -------------------------------

async def billing_rollup(session: AsyncSession) -> dict[str, Any]:
    row = (await session.execute(
        text("SELECT mrr_minor, charges_revenue_minor, charges_cost_minor, margin_minor, "
             "active_clients FROM platform_billing_rollup()"))).mappings().one()
    return dict(row)
