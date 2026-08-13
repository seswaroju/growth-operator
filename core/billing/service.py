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
    if (config or {}).get("preset_key") is not None:
        # Only the seeder mints canonical identity; an operator must not be able to forge a plan
        # that then becomes uneditable and looks code-managed.
        raise CanonicalPresetLocked(str((config or {})["preset_key"]))
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


class CanonicalPresetLocked(Exception):
    """A canonical Recover/Grow/Scale row was edited through the generic CP-1 plan editor.

    That editor predates the PLAN-2 structured contract: its payload rebuilds `config` from only
    `agents`/`channels`/`addons`, and this module replaces the whole JSONB — so changing nothing but
    a price would strip `entitlement_schema_version`, `entitlements`, `promotions` and the preset
    identity, silently demoting a structured plan back to legacy. Canonical presets are
    code-managed; customising one means copying it (PLAN-4), which yields an ordinary plan
    carrying no `preset_key`."""

    def __init__(self, preset_key: str):
        self.preset_key = preset_key
        super().__init__(
            f"{preset_key!r} is a canonical preset and is code-managed; "
            "copy and customise it through Plan Builder instead of editing it")


async def _stored_preset_key(session: AsyncSession, plan_id: UUID) -> str | None:
    row = (await session.execute(
        text("SELECT config->>'preset_key' AS k FROM billing_plans WHERE id = :id"),
        {"id": plan_id})).mappings().first()
    return None if row is None else row["k"]


async def update_plan(
    session: AsyncSession, plan_id: UUID, *, name: str, price_minor: int, active: bool,
    description: str | None, features: list[str],
    max_managers: int = 0, max_staff: int = 0, config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Full update of a plan (editable, CP-1). Returns the updated row, or None if no plan has
    that id. Raises `CanonicalPresetLocked` for a code-managed preset — the check reads the
    **stored** row, so it also blocks retiring one via `active=False` or stripping its identity."""
    # Lock first, then decide — see `_LOCK_PLAN_SQL`.
    if await _lock_plan(session, plan_id) is None:
        return None
    existing_key = await _stored_preset_key(session, plan_id)
    if existing_key:
        raise CanonicalPresetLocked(existing_key)
    if await plan_has_been_sold(session, plan_id):
        raise SoldPlanImmutable(plan_id)
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
    """Put the client on a plan: verify the target, then cancel the old and start the new one.

    The target is validated **before** anything is cancelled — a rejected assignment must never
    leave a store with no subscription. `active = false` means retired: unavailable for new
    assignment, while existing subscribers keep resolving their plan (PLAN-3/PLAN-4).

    The plan row is locked first, the same serialization point every commercial mutation uses, so
    an assignment and a retire/edit of that plan cannot interleave."""
    row = await _lock_plan(session, plan_id)
    if row is None:
        raise PlanNotAssignable(plan_id, "no such plan")
    if not row["active"]:
        raise PlanNotAssignable(plan_id, "plan is retired (active = false)")

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


# ---- PLAN-4: commercial-history protection ------------------------------------------------------

# Every plan mutation and every subscription assignment takes this lock on the plan row **first**,
# then decides. Checking sold-history before locking would leave the race the whole guard exists to
# prevent: a subscriber created between the check and the update, whose purchased terms are then
# silently rewritten.
_LOCK_PLAN_SQL = text("SELECT id, active FROM billing_plans WHERE id = :id FOR UPDATE")
# One definition of "sold", answered by the SECURITY DEFINER function (migration 051). The ordinary
# request session is RLS-scoped and would see zero subscriptions, so a direct query here would
# report every plan as never-sold.
_SOLD_SQL = text("SELECT public.plan_has_subscription_history(CAST(:id AS uuid))")


class SoldPlanImmutable(Exception):
    """A plan that has ever been subscribed to may only have `active` changed.

    Price, entitlements, limits *and* name/description/display copy are all historical commercial
    truth — they surface in invoices, operator records and subscriber history — so rewriting them in
    place would change what a merchant already bought. The supported path is copy → edit the new
    snapshot → assign."""

    def __init__(self, plan_id: UUID):
        self.plan_id = plan_id
        super().__init__(
            "this plan has been subscribed to; only its active flag may change. "
            "Copy it, edit the copy, then assign that plan instead")


class PlanNotAssignable(Exception):
    """The requested plan is missing or retired (`active = false`).

    Raised **before** anything is cancelled: a failed assignment must never leave a store with no
    subscription at all."""

    def __init__(self, plan_id: UUID, reason: str):
        self.plan_id = plan_id
        self.reason = reason
        super().__init__(f"cannot assign plan {plan_id}: {reason}")


async def plan_has_been_sold(session: AsyncSession, plan_id: UUID) -> bool:
    return bool((await session.execute(_SOLD_SQL, {"id": str(plan_id)})).scalar_one())


async def _lock_plan(session: AsyncSession, plan_id: UUID) -> Any | None:
    return (await session.execute(_LOCK_PLAN_SQL, {"id": plan_id})).mappings().first()


async def set_plan_active(
    session: AsyncSession, plan_id: UUID, *, active: bool
) -> dict[str, Any] | None:
    """Retire or reinstate a plan. Allowed on a sold plan — it governs eligibility for *future*
    assignment and changes nothing an existing subscriber resolves. Canonical rows stay locked."""
    if await _lock_plan(session, plan_id) is None:
        return None
    existing_key = await _stored_preset_key(session, plan_id)
    if existing_key:
        raise CanonicalPresetLocked(existing_key)
    row = (await session.execute(
        text(f"UPDATE billing_plans SET active = :a WHERE id = :id RETURNING {_PLAN_COLS}"),
        {"a": active, "id": plan_id})).mappings().one_or_none()
    return dict(row) if row is not None else None


async def update_plan_structured(
    session: AsyncSession, plan_id: UUID, *, name: str, price_minor: int,
    description: str | None, config: dict[str, Any], max_managers: int, max_staff: int,
) -> dict[str, Any] | None:
    """Structured edit of a custom plan (PLAN-4). `features` is forced empty — machine authority
    lives in `config`, never in the legacy display column."""
    if await _lock_plan(session, plan_id) is None:
        return None
    existing_key = await _stored_preset_key(session, plan_id)
    if existing_key:
        raise CanonicalPresetLocked(existing_key)
    if await plan_has_been_sold(session, plan_id):
        raise SoldPlanImmutable(plan_id)
    row = (await session.execute(
        text("UPDATE billing_plans SET name = :n, price_minor = :p, description = :d, "
             "features = '[]'::jsonb, max_managers = :mm, max_staff = :ms, "
             f"config = CAST(:cfg AS jsonb) WHERE id = :id RETURNING {_PLAN_COLS}"),
        {"id": plan_id, "n": name, "p": price_minor, "d": description,
         "mm": max_managers, "ms": max_staff, "cfg": json.dumps(config)})).mappings().one_or_none()
    return dict(row) if row is not None else None


async def copy_plan(
    session: AsyncSession, plan_id: UUID, *, name: str | None = None
) -> dict[str, Any] | None:
    """Copy any plan into a new, independent custom plan.

    The copy is a **snapshot**: canonical identity (`preset_key` / `preset_version`) is stripped, so
    the seeder can never regard it as canonical and a later change to the canonical definition can
    never reach it. A **legacy** source (no `entitlement_schema_version`) is converted through the
    same compatibility reconstruction the resolver uses, rather than being reinterpreted in place —
    the source row is not modified either way."""
    from core.billing.presets import all_presets
    from core.tenancy.entitlements import (
        _LEGACY_ENT1A_BASELINE,
        implied_legacy_channels,
        normalize,
    )
    from core.tenancy.plan_config import parse_plan_config

    src = (await session.execute(
        text(f"SELECT {_PLAN_COLS} FROM billing_plans WHERE id = :id"), {"id": plan_id})
    ).mappings().first()
    if src is None:
        return None

    config = parse_plan_config(src["config"])
    cfg = dict(config.model_dump(exclude_none=False))
    cfg.pop("preset_key", None)
    cfg.pop("preset_version", None)

    if not config.is_structured:
        # Legacy → structured: reconstruct exactly what the resolver would have granted.
        caps = _LEGACY_ENT1A_BASELINE | normalize(src["features"])
        cfg["entitlement_schema_version"] = 1
        cfg["entitlements"] = sorted(caps)
        cfg["channels"] = sorted(set(cfg.get("channels") or []) | implied_legacy_channels(caps))
        cfg.setdefault("agents", [])
        cfg.setdefault("addons", [])
        cfg.setdefault("promotions", [])
    if cfg.get("vertical") is None and (key := (config.model_extra or {}).get("preset_key")):
        # Older canonical snapshots predate persisted `config.vertical`. Recover it by exact
        # preset-key lookup — never by parsing a name or splitting the key on a separator.
        match = next((p for p in all_presets() if p.preset_key == key), None)
        if match is not None:
            cfg["vertical"] = match.vertical

    base = name or f"{src['name']} (copy)"
    for attempt in range(1, 50):
        candidate = base if attempt == 1 else f"{base} {attempt}"
        exists = (await session.execute(
            text("SELECT 1 FROM billing_plans WHERE name = :n"), {"n": candidate})).first()
        if exists is None:
            break
    else:  # pragma: no cover - 50 identically-named copies is not a real scenario
        raise ValueError("could not find a free name for the copy")

    row = (await session.execute(
        text("INSERT INTO billing_plans (name, price_minor, active, description, features, "
             "max_managers, max_staff, config) VALUES (:n, :p, true, :d, '[]'::jsonb, :mm, :ms, "
             f"CAST(:cfg AS jsonb)) RETURNING {_PLAN_COLS}"),
        {"n": candidate, "p": src["price_minor"], "d": src["description"],
         "mm": src["max_managers"], "ms": src["max_staff"], "cfg": json.dumps(cfg)})
    ).mappings().one()
    return dict(row)
