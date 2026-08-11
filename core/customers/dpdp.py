"""DPDP data-subject requests for a customer (CRM depth, D3).

`export_customer` — the **right to access**: a contact's complete record (profile + every linked
row: leads, conversations & messages, orders, quotes, notes, tags, campaign touches) as one JSON.
`erase_customer` — the **right to erasure**: hard-deletes the contact, which cascades every linked
row (all `contact_id` FKs are `ON DELETE CASCADE`), and audits it as a fulfilled DSR **before** the
delete (log-then-act; the audit row is org-scoped, so it survives). No PII is written to the audit.

Both are org-scoped two ways (RLS + explicit `org_id`) and verify the contact is the caller's org
first, so one org can never export or erase another's customer.

**Retention note:** erasure currently deletes financial/analytics rows (orders, leads) with the PII.
Whether to instead retain-but-anonymise under a legal-retention exception is a founder policy
decision — see BLOCKERS #24.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import AuditEntry
from core.audit import write as audit_write
from core.audit.taxonomy import DSR_FULFILLED
from core.tenancy.repository import set_org_context


async def _owns_contact(session: AsyncSession, org_id: UUID, contact_id: UUID) -> bool:
    row = (
        await session.execute(
            text("SELECT 1 FROM contacts WHERE id = :c AND org_id = :o"),
            {"c": str(contact_id), "o": str(org_id)},
        )
    ).first()
    return row is not None


async def _rows(session: AsyncSession, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(r) for r in (await session.execute(text(sql), params)).mappings().all()]


async def export_customer(
    session: AsyncSession, org_id: UUID, contact_id: UUID
) -> dict[str, Any] | None:
    """A contact's complete record for a DPDP access request. `None` if not the org's contact."""
    await set_org_context(session, org_id)
    profile = (
        await session.execute(
            text("SELECT * FROM contacts WHERE id = :c AND org_id = :o"),
            {"c": str(contact_id), "o": str(org_id)},
        )
    ).mappings().first()
    if profile is None:
        return None
    p = {"c": str(contact_id), "o": str(org_id)}
    return {
        "profile": dict(profile),
        "leads": await _rows(
            session, "SELECT * FROM leads WHERE contact_id = :c AND org_id = :o "
            "ORDER BY created_at", p),
        "conversations": await _rows(
            session, "SELECT * FROM conversations WHERE contact_id = :c AND org_id = :o "
            "ORDER BY created_at", p),
        "messages": await _rows(
            session, "SELECT m.* FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE c.contact_id = :c AND m.org_id = :o ORDER BY m.created_at", p),
        "orders": await _rows(
            session, "SELECT * FROM orders WHERE contact_id = :c AND org_id = :o "
            "ORDER BY created_at", p),
        "quotes": await _rows(
            session, "SELECT q.* FROM quotes q JOIN conversations c ON c.id = q.conversation_id "
            "WHERE c.contact_id = :c AND q.org_id = :o ORDER BY q.created_at", p),
        "notes": await _rows(
            session, "SELECT * FROM customer_notes WHERE contact_id = :c AND org_id = :o "
            "ORDER BY created_at", p),
        "tags": await _rows(
            session, "SELECT tag, created_at FROM contact_tags WHERE contact_id = :c "
            "AND org_id = :o ORDER BY tag", p),
        "campaign_touches": await _rows(
            session, "SELECT * FROM campaign_touches WHERE contact_id = :c AND org_id = :o "
            "ORDER BY occurred_at", p),
    }


async def erase_customer(
    session: AsyncSession, org_id: UUID, contact_id: UUID, *, actor_id: UUID
) -> bool | None:
    """Hard-delete the contact (cascades every linked row) for a DPDP erasure request. Audits the
    fulfilled DSR first (no PII in the audit). `True` when erased, `None` if not the org's."""
    await set_org_context(session, org_id)
    if not await _owns_contact(session, org_id, contact_id):
        return None
    await audit_write(session, AuditEntry(
        org_id=org_id, actor_type="user", action=DSR_FULFILLED, actor_id=str(actor_id),
        resource=f"contact:{contact_id}",
        payload={"request": "erasure", "contact_id": str(contact_id)}))  # no PII
    await session.execute(
        text("DELETE FROM contacts WHERE id = :c AND org_id = :o"),
        {"c": str(contact_id), "o": str(org_id)},
    )
    return True
