"""Availability transitions + price-input staleness (MVP-049).

Two vertical-agnostic mechanisms over `catalog_items`:

1. **Availability transitions** — a small validated state graph; every transition is appended to
   the org's audit chain (so an agent-actor change is attributable).
2. **Price-input staleness** — when a catalog attribute that the tenant's pricing rules actually
   reference changes, open quotes computed from that item are flagged `stale_inputs` so the
   concierge recomputes before re-asserting a figure. Which attributes matter is derived from the
   strategy's rule ASTs (never hard-coded), keeping this module free of any industry noun.

The typed `catalog.price_inputs_changed.v1` event is now registered (topics.yaml) and **emitted**
in the same transaction as the flag write, so other consumers (pricing cache, workflows) can react
(BLOCKER #17 resolved). The flag itself (the MVP-visible signal) is still written synchronously.
"""

from __future__ import annotations

import ast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.writer import AuditEntry
from core.audit.writer import write as audit_write
from core.events.outbox import emit
from core.pricing.engine import to_python

AVAILABILITY_STATES = frozenset({"in_stock", "made_to_order", "bookable_slot", "out"})
AVAILABILITY_CHANGED_ACTION = "catalog.availability_changed"

# Allowed transitions. `bookable_slot` (clinic) is out of MVP scope, so it is neither a source
# nor a target here — an item created in another state cannot move into it.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "in_stock": frozenset({"made_to_order", "out"}),
    "made_to_order": frozenset({"in_stock", "out"}),
    "out": frozenset({"in_stock", "made_to_order"}),
}


class InvalidTransition(Exception):
    """The requested availability transition is not allowed from the current state."""


class ItemNotFound(Exception):
    """No catalog item with that id in the caller's org."""


def _input_refs(formula: str) -> set[str]:
    """The `inputs.<field>` / `inputs['field']` / `inputs.get('field', …)` names a formula reads."""
    tree = ast.parse(to_python(formula), mode="eval")
    refs: set[str] = set()
    for node in ast.walk(tree):
        # inputs.get('field', default) — the field is the first (string) arg, not the method name.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "inputs"
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            refs.add(node.args[0].value)
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "inputs"
            and node.attr != "get"  # the .get() method itself is not a field
        ):
            refs.add(node.attr)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "inputs"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            refs.add(node.slice.value)
    return refs


def price_input_deps(strategy: dict) -> set[str]:
    """Every catalog input field the strategy's stage formulas reference (its price inputs)."""
    deps: set[str] = set()
    for stage in strategy.get("rules", {}).get("stages", []):
        deps |= _input_refs(stage["formula"])
    return deps


async def transition(
    session: AsyncSession, org_id: UUID, item_id: UUID, to_state: str, *,
    actor_id: UUID, actor_type: str = "user", reason: str = "",
) -> str:
    """Move an item to `to_state` (validated) and append the change to the audit chain."""
    if to_state not in AVAILABILITY_STATES:
        raise InvalidTransition(f"unknown availability state {to_state!r}")
    current = (
        await session.execute(
            text("SELECT availability FROM catalog_items WHERE id = :id"), {"id": str(item_id)}
        )
    ).scalar_one_or_none()
    if current is None:
        raise ItemNotFound(str(item_id))
    if to_state != current and to_state not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransition(f"{current!r} -> {to_state!r} not allowed")

    await session.execute(
        text("UPDATE catalog_items SET availability = :s, updated_at = now() WHERE id = :id"),
        {"s": to_state, "id": str(item_id)},
    )
    await audit_write(
        session,
        AuditEntry(
            org_id=org_id, actor_type=actor_type, actor_id=str(actor_id),
            action=AVAILABILITY_CHANGED_ACTION, resource=str(item_id),
            payload={"from": current, "to": to_state, "reason": reason},
        ),
    )
    return to_state


async def _pack_strategies(session: AsyncSession, pack_id: UUID) -> list[dict]:
    rows = (
        await session.execute(
            text("SELECT rules FROM pricing_strategies WHERE pack_id = :p"), {"p": str(pack_id)}
        )
    ).mappings().all()
    return [dict(r["rules"] or {}) for r in rows]


async def flag_stale_quotes_for_item(session: AsyncSession, org_id: UUID, item_id: UUID) -> int:
    """Flag open (draft, unexpired) quotes computed from this item as `stale_inputs`. Returns the
    number flagged. A quote references an item when its stored inputs carry that ``item_id``."""
    flagged = (
        await session.execute(
            text(
                "UPDATE quotes SET stale_inputs = true "
                "WHERE org_id = :o AND status = 'draft' AND stale_inputs = false "
                "AND (valid_until IS NULL OR valid_until > now()) "
                "AND inputs -> 'inputs' ->> 'item_id' = :item RETURNING id"
            ),
            {"o": str(org_id), "item": str(item_id)},
        )
    ).scalars().all()
    return len(flagged)


async def flag_quotes_if_price_inputs_changed(
    session: AsyncSession, org_id: UUID, item_id: UUID, pack_id: UUID, changed_keys: set[str]
) -> int:
    """If any changed attribute is a price input for the pack's strategies, flag dependent open
    quotes. A change to an attribute no rule reads (e.g. a description) flags nothing."""
    if not changed_keys:
        return 0
    deps: set[str] = set()
    for strategy in await _pack_strategies(session, pack_id):
        deps |= price_input_deps(strategy)
    changed_price_inputs = changed_keys & deps
    if not changed_price_inputs:
        return 0
    # A real price input changed → emit the typed event in the same txn (#17), then flag quotes.
    await emit(
        session, org_id=org_id, event_type="catalog.price_inputs_changed.v1", source="catalog",
        payload={"item_id": str(item_id), "changed_keys": sorted(changed_price_inputs)})
    return await flag_stale_quotes_for_item(session, org_id, item_id)
