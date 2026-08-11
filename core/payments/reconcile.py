"""Razorpay payment-confirmation sweep (PAY3b).

The deferred half of the capture webhook (`core/payments/webhook.py`). Reads unprocessed `razorpay`
rows from the global `webhook_events`, maps each **paid** event to a transaction via the signature-
verified `notes` (`org_id` + `tx_id`, which we set on the payment link), and — if the transaction is
still `created` — marks it paid and drafts the PAY3 `receipt.send` approval. The receipt still only
goes out once that approval is granted (§10.4 / §19).

**Idempotent** three ways: the webhook dedupes on `(provider, external_id)`; a processed row is
never re-swept (`processed_at`); and the `status == 'created'` guard means two capture events for
one transaction (e.g. `payment.captured` then `payment_link.paid`) draft the approval at most once.
A row we can't map (not a paid event, or missing/invalid notes) is marked processed, not retried.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text

from core.common.db import get_sessionmaker
from core.payments import delivery
from core.payments import transactions as tx_service
from core.tenancy.middleware import org_scoped_session

logger = logging.getLogger("core.payments.reconcile")

# Razorpay capture events that mean "the money arrived".
_PAID_EVENTS = frozenset({"payment_link.paid", "payment.captured", "order.paid"})


def payment_mapping(payload: dict[str, Any]) -> tuple[UUID, UUID] | None:
    """(org_id, tx_id) from a paid event's signed `notes`; None if not a mappable capture."""
    if payload.get("event") not in _PAID_EVENTS:
        return None
    entities = payload.get("payload") or {}
    for key in ("payment_link", "payment", "order"):
        entity = (entities.get(key) or {}).get("entity") or {}
        notes = entity.get("notes")
        if isinstance(notes, dict) and notes.get("org_id") and notes.get("tx_id"):
            try:
                return UUID(str(notes["org_id"])), UUID(str(notes["tx_id"]))
            except ValueError:
                return None
    return None


async def _mark_processed_global(event_id: UUID) -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        await session.execute(
            text("UPDATE webhook_events SET processed_at = now() WHERE id = :id"), {"id": event_id})
        await session.commit()


async def _confirm_one(event_id: UUID, payload: dict[str, Any]) -> bool:
    """Confirm one webhook. Returns True if it marked a transaction paid (drafted the approval)."""
    mapping = payment_mapping(payload)
    if mapping is None:  # not a paid event / no usable notes — handled, don't retry
        await _mark_processed_global(event_id)
        return False
    org_id, tx_id = mapping
    drafted = False
    async with org_scoped_session(org_id) as session:
        tx = await tx_service.get_transaction(session, org_id, tx_id)
        if tx is not None and tx["status"] == "created":
            await delivery.mark_paid_and_request_receipt(
                session, org_id, tx, requested_by=None)
            drafted = True
        # Mark processed in the same tx (webhook_events is global — RLS-exempt).
        await session.execute(
            text("UPDATE webhook_events SET processed_at = now() WHERE id = :id"), {"id": event_id})
    return drafted


async def confirm_pending_razorpay(limit: int = 100) -> int:
    """Process a batch of unprocessed Razorpay capture webhooks. Returns the number that drafted a
    receipt approval (a freshly-confirmed payment)."""
    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            text(
                "SELECT id, payload FROM webhook_events "
                "WHERE provider = 'razorpay' AND processed_at IS NULL "
                "AND payload->>'_malformed' IS NULL "
                "ORDER BY received_at LIMIT :n"),
            {"n": limit})).mappings().all()
        events = [(r["id"], r["payload"]) for r in rows]

    drafted = 0
    for event_id, payload in events:
        try:
            if await _confirm_one(event_id, payload):
                drafted += 1
        except Exception:
            logger.exception("razorpay reconcile failed for webhook %s", event_id)
    return drafted


def register_jobs() -> None:
    """Register the every-minute Razorpay confirmation sweep."""
    from core.events import scheduler as sched

    async def _sweep() -> None:
        await confirm_pending_razorpay()

    sched.register("razorpay_webhook_sweep", "* * * * *", _sweep)
