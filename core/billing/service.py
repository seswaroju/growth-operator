"""Billing service (B1) — OPERATOR-owned per-client revenue records.

Plans are a global GO catalog; subscriptions + charges are org-scoped (RLS). The operator writes a
client's subscription/charge by scoping the session to that target org (``set_org_context``) — a
normal scoped write, no `app.platform_admin` flag involved. The cross-client aggregate for the
dashboard comes from the ``platform_billing_rollup()`` SECURITY DEFINER function (sums only).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import budgets
from core.tenancy.repository import set_org_context

_PLAN_COLS = ("id, name, price_minor, active, description, features, "
              "max_managers, max_staff, config, created_at")
_CHARGE_COLS = "id, org_id, period_month, charge_type, amount_minor, cost_minor, note, created_at"


# ---- Plans (global GO catalog) -----------------------------------------------------------------

async def create_plan(
    session: AsyncSession, *, name: str, price_minor: int,
    description: str | None = None, features: list[str] | None = None,
    max_managers: int = 0, max_staff: int = 0, config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = (await session.execute(
        text("INSERT INTO billing_plans "
             "(name, price_minor, description, features, max_managers, max_staff, config) "
             "VALUES (:n, :p, :d, CAST(:f AS jsonb), :mm, :ms, CAST(:cfg AS jsonb)) "
             f"RETURNING {_PLAN_COLS}"),
        {"n": name, "p": price_minor, "d": description, "f": json.dumps(features or []),
         "mm": max_managers, "ms": max_staff,
         "cfg": json.dumps(config or {})})).mappings().one()
    return dict(row)


async def list_plans(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (await session.execute(
        text(f"SELECT {_PLAN_COLS} FROM billing_plans ORDER BY price_minor"))).mappings().all()
    return [dict(r) for r in rows]


async def update_plan(
    session: AsyncSession, plan_id: UUID, *, name: str, price_minor: int, active: bool,
    description: str | None, features: list[str],
    max_managers: int = 0, max_staff: int = 0, config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Full update of a plan (editable, CP-1). Returns the updated row, or None if no plan has
    that id."""
    row = (await session.execute(
        text("UPDATE billing_plans SET name = :n, price_minor = :p, active = :a, "
             "description = :d, features = CAST(:f AS jsonb), max_managers = :mm, "
             "max_staff = :ms, config = CAST(:cfg AS jsonb) WHERE id = :id "
             f"RETURNING {_PLAN_COLS}"),
        {"id": plan_id, "n": name, "p": price_minor, "a": active, "d": description,
         "f": json.dumps(features), "mm": max_managers, "ms": max_staff,
         "cfg": json.dumps(config or {})})).mappings().one_or_none()
    return dict(row) if row is not None else None


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
    # OC7: block the charge if it would push this channel over an enforced monthly cap.
    await budgets.check_and_enforce(session, org_id, charge_type, amount_minor, on=period_month)
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


async def monthly_spend_by_channel(
    session: AsyncSession, org_id: UUID, period_month: date
) -> list[dict[str, Any]]:
    """The org's own spend grouped by channel for a month, biggest first (OC6, owner-facing).

    **AMOUNT only** — never `cost_minor`: that's GO's internal cost/margin and must never reach the
    store owner. RLS-scoped to the caller's org.
    """
    await set_org_context(session, org_id)
    rows = (await session.execute(
        text("SELECT charge_type, COALESCE(SUM(amount_minor), 0) AS amount_minor "
             "FROM billing_charges WHERE org_id = :o "
             "AND date_trunc('month', period_month) = date_trunc('month', CAST(:pm AS date)) "
             "GROUP BY charge_type ORDER BY amount_minor DESC"),
        {"o": str(org_id), "pm": period_month})).mappings().all()
    return [dict(r) for r in rows]


# ---- Cross-client aggregate for the Financial dashboard (SECDEF) -------------------------------

async def billing_rollup(session: AsyncSession) -> dict[str, Any]:
    row = (await session.execute(
        text("SELECT mrr_minor, charges_revenue_minor, charges_cost_minor, margin_minor, "
             "active_clients FROM platform_billing_rollup()"))).mappings().one()
    return dict(row)
