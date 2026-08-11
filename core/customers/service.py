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
                WHERE ct.org_id = :o AND ct.erased_at IS NULL
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


async def customer_timeline(
    session: AsyncSession, org_id: UUID, contact_id: UUID, *, limit: int = 100
) -> list[dict[str, Any]] | None:
    """One contact's **unified activity feed** (D1): messages, quotes, orders, leads and campaign
    touches merged into one list, newest first. Each entry is a typed `{kind, occurred_at, ref_id,
    detail}`. Returns `None` when the contact isn't the org's (→ 404). Read-only; RLS + explicit
    `org_id` filter scope every branch."""
    await set_org_context(session, org_id)
    owns = (
        await session.execute(
            text("SELECT 1 FROM contacts WHERE id = :id AND org_id = :o"),
            {"id": str(contact_id), "o": str(org_id)},
        )
    ).first()
    if owns is None:
        return None
    rows = (
        await session.execute(
            text(
                """
                SELECT kind, occurred_at, ref_id, detail FROM (
                  SELECT 'message' AS kind, m.created_at AS occurred_at, m.id AS ref_id,
                         jsonb_build_object('direction', m.direction, 'status', m.status,
                                            'preview', left(coalesce(m.body, ''), 140)) AS detail
                  FROM messages m JOIN conversations c ON c.id = m.conversation_id
                  WHERE c.contact_id = :c AND m.org_id = :o
                  UNION ALL
                  SELECT 'quote', q.created_at, q.id,
                         jsonb_build_object('total_minor', q.total_minor, 'currency', q.currency)
                  FROM quotes q JOIN conversations c ON c.id = q.conversation_id
                  WHERE c.contact_id = :c AND q.org_id = :o
                  UNION ALL
                  SELECT 'order', o.created_at, o.id,
                         jsonb_build_object('total_minor', o.total_minor, 'status', o.status)
                  FROM orders o WHERE o.contact_id = :c AND o.org_id = :o
                  UNION ALL
                  SELECT 'lead', l.created_at, l.id,
                         jsonb_build_object('stage', l.stage, 'source', l.source)
                  FROM leads l WHERE l.contact_id = :c AND l.org_id = :o
                  UNION ALL
                  SELECT 'campaign_touch', ct.occurred_at, ct.id,
                         jsonb_build_object('campaign_id', ct.campaign_id::text)
                  FROM campaign_touches ct WHERE ct.contact_id = :c AND ct.org_id = :o
                  UNION ALL
                  SELECT 'note', n.created_at, n.id,
                         jsonb_build_object('preview', left(n.body, 140))
                  FROM customer_notes n WHERE n.contact_id = :c AND n.org_id = :o
                ) tl
                ORDER BY occurred_at DESC, kind
                LIMIT :lim
                """
            ),
            {"c": str(contact_id), "o": str(org_id), "lim": limit},
        )
    ).mappings().all()
    return [dict(r) for r in rows]
