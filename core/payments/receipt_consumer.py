"""Receipt-delivery consumer (PAY3) — the second half of the approval gate.

Registered on `approval.resolved.v1` as its own consumer group ("receipt-delivery"), independent
of the runtime-resume consumer on the same stream. On an **approved** `receipt.send` approval it
renders and sends the receipt. The consumer framework dedupes redeliveries (per event id) and
`deliver_receipt` is idempotent (a `receipted` transaction is a no-op), so a receipt goes out at
most once. A rejected approval sends nothing.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text

from core.events.consumer import consumer
from core.events.topics import stream_name
from core.payments.delivery import RECEIPT_ACTION, deliver_receipt
from core.tenancy.middleware import org_scoped_session

RECEIPT_STREAM = stream_name("approval.resolved.v1")


@consumer(RECEIPT_STREAM, "receipt-delivery")
async def on_receipt_approval_resolved(envelope: dict[str, Any]) -> None:
    org_id = UUID(str(envelope["subject"]))
    data = envelope.get("data") or {}
    if data.get("decision") != "approved":  # rejected/expired → nothing sends
        return
    approval_id = data.get("approval_id")
    if not approval_id:
        return
    async with org_scoped_session(org_id) as s:
        row = (
            await s.execute(
                text("SELECT action_type, payload, edited_payload FROM approvals WHERE id = :id"),
                {"id": approval_id},
            )
        ).mappings().first()
        if row is None or row["action_type"] != RECEIPT_ACTION:  # different action_type — not ours
            return
        payload = row["edited_payload"] or row["payload"] or {}
        tx_id = payload.get("transaction_id")
        if not tx_id:
            return
        await deliver_receipt(s, org_id, UUID(str(tx_id)))
