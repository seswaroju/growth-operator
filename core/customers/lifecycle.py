"""Lead lifecycle transitions (GHOST-1a).

The missing producer: nothing advanced a lead's stage or emitted `lead.stage_changed.v1`, so the
workflow engine's lead-triggered playbooks — above all **ghost recovery** — had no ignition and
could never fire for a real store. This module is that ignition.

`mark_quoted()` is called from the **send path** once an approved, ledgered quote has actually
reached the customer (founder decision 2026-08-12: transition on *delivery*, not on computing a
quote — a lead must never be treated as "quoted" for a draft the owner rejected).

Generic/platform-invariant: stages and leads are CRM concepts, not industry nouns (Rule Zero).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events.outbox import emit

STAGE_CHANGED_EVENT = "lead.stage_changed.v1"


async def _open_lead(
    session: AsyncSession, org_id: UUID, contact_id: UUID
) -> dict[str, Any] | None:
    """The contact's most recent non-terminal lead (RLS-scoped by the caller's context)."""
    row = (
        await session.execute(
            text("SELECT id, stage FROM leads "
                 "WHERE org_id = :o AND contact_id = :c AND stage NOT IN ('won','lost') "
                 "ORDER BY created_at DESC LIMIT 1"),
            {"o": str(org_id), "c": str(contact_id)})
    ).mappings().first()
    return dict(row) if row else None


async def mark_quoted(
    session: AsyncSession, org_id: UUID, *, contact_id: UUID, message_id: UUID | None = None,
) -> UUID | None:
    """A ledgered quote just reached this contact → advance their open lead to `quoted`.

    Stamps the outbound-touch columns the diagnosis playbooks read (`last_outbound_msg_at`,
    `last_message_direction`, `last_touch_at`) and emits **`lead.stage_changed.v1`** in the caller's
    transaction (transactional outbox), which is what starts ghost recovery downstream.

    Idempotent: a lead already at `quoted` still gets its outbound touch refreshed but does **not**
    re-emit, so a second quote can't spawn a duplicate recovery run. Returns the lead id, or None
    when the contact has no open lead."""
    lead = await _open_lead(session, org_id, contact_id)
    if lead is None:
        return None
    lead_id = UUID(str(lead["id"]))
    already_quoted = lead["stage"] == "quoted"

    await session.execute(
        text("UPDATE leads SET stage = 'quoted', last_outbound_msg_at = now(), "
             "last_message_direction = 'outbound', last_touch_at = now(), updated_at = now() "
             "WHERE id = :id"),
        {"id": str(lead_id)})
    if already_quoted:
        return lead_id  # touch refreshed; no duplicate transition event

    row = (
        await session.execute(
            text("SELECT last_customer_msg_at FROM leads WHERE id = :id"), {"id": str(lead_id)})
    ).mappings().first()
    last_customer = row["last_customer_msg_at"] if row else None
    payload: dict[str, Any] = {
        "lead_id": str(lead_id),
        "stage": "quoted",
        "last_customer_msg_at": last_customer.isoformat() if last_customer else None,
        # subject extras the playbooks bind against (contact + the message that carried the quote)
        "contact_id": str(contact_id),
        "message_id": str(message_id) if message_id else None,
        "previous_stage": lead["stage"],
    }
    await emit(session, org_id=org_id, event_type=STAGE_CHANGED_EVENT, payload=payload,
               source="crm")
    return lead_id
