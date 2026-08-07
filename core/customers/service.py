"""Customers (CRM) read-model (Phase 3, Ticket 3.5).

Read-only views: the customer list (each with lead/order counts) and one customer's profile +
history — their leads, conversations, and orders. Org-scoped two ways (RLS via `set_org_context`
+ an explicit `org_id` filter). No writes.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context


async def list_customers(session: AsyncSession, org_id: UUID) -> list[dict[str, Any]]:
    """All contacts for the org, newest first, with lead + order counts."""
    await set_org_context(session, org_id)
    rows = (
        await session.execute(
            text(
                """
                SELECT ct.id, ct.full_name, ct.phone, ct.email, ct.consent_status, ct.created_at,
                       (SELECT count(*) FROM leads l WHERE l.contact_id = ct.id)  AS lead_count,
                       (SELECT count(*) FROM orders o WHERE o.contact_id = ct.id) AS order_count
                FROM contacts ct
                WHERE ct.org_id = :o
                ORDER BY ct.created_at DESC
                LIMIT 200
                """
            ),
            {"o": str(org_id)},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def get_customer(
    session: AsyncSession, org_id: UUID, contact_id: UUID
) -> dict[str, Any] | None:
    """One contact's profile + history (leads, conversations, orders). None if not the org's."""
    await set_org_context(session, org_id)
    profile = (
        await session.execute(
            text(
                "SELECT id, full_name, phone, email, language_pref, consent_status, "
                "       attributes, created_at "
                "FROM contacts WHERE id = :id AND org_id = :o"
            ),
            {"id": str(contact_id), "o": str(org_id)},
        )
    ).mappings().first()
    if profile is None:
        return None
    leads = (
        await session.execute(
            text(
                "SELECT id, stage, source, score, created_at FROM leads "
                "WHERE contact_id = :id AND org_id = :o ORDER BY created_at DESC"
            ),
            {"id": str(contact_id), "o": str(org_id)},
        )
    ).mappings().all()
    convos = (
        await session.execute(
            text(
                "SELECT id, status, updated_at FROM conversations "
                "WHERE contact_id = :id AND org_id = :o ORDER BY updated_at DESC"
            ),
            {"id": str(contact_id), "o": str(org_id)},
        )
    ).mappings().all()
    orders = (
        await session.execute(
            text(
                "SELECT id, status, total_minor, currency, created_at FROM orders "
                "WHERE contact_id = :id AND org_id = :o ORDER BY created_at DESC"
            ),
            {"id": str(contact_id), "o": str(org_id)},
        )
    ).mappings().all()
    return {
        **dict(profile),
        "leads": [dict(r) for r in leads],
        "conversations": [dict(r) for r in convos],
        "orders": [dict(r) for r in orders],
    }
