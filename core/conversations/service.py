"""Conversations & leads read-model (Phase 3, Ticket 3.3).

Read-only views the customer app renders — the conversation inbox, one thread with its messages,
and the lead pipeline. Everything is org-scoped two ways (RLS via `set_org_context` + an explicit
`org_id` filter), so a store only ever sees its OWN customers. No writes here.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context


async def list_conversations(session: AsyncSession, org_id: UUID) -> list[dict[str, Any]]:
    """The inbox: recent conversations, each with its contact + a last-message preview."""
    await set_org_context(session, org_id)
    rows = (
        await session.execute(
            text(
                """
                SELECT c.id, c.status, c.outcome, c.updated_at,
                       ct.full_name AS contact_name, ct.phone AS contact_phone,
                       (SELECT count(*) FROM messages m WHERE m.conversation_id = c.id)
                           AS message_count,
                       lm.body AS last_body, lm.direction AS last_direction,
                       lm.created_at AS last_at
                FROM conversations c
                LEFT JOIN contacts ct ON ct.id = c.contact_id
                LEFT JOIN LATERAL (
                    SELECT body, direction, created_at FROM messages m
                    WHERE m.conversation_id = c.id ORDER BY created_at DESC LIMIT 1
                ) lm ON true
                WHERE c.org_id = :o
                ORDER BY c.updated_at DESC
                LIMIT 100
                """
            ),
            {"o": str(org_id)},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def get_conversation(
    session: AsyncSession, org_id: UUID, conversation_id: UUID
) -> dict[str, Any] | None:
    """One thread — header + messages ascending. None if it isn't the caller's org's."""
    await set_org_context(session, org_id)
    head = (
        await session.execute(
            text(
                """
                SELECT c.id, c.status, c.outcome, c.created_at, c.updated_at,
                       ct.full_name AS contact_name, ct.phone AS contact_phone
                FROM conversations c
                LEFT JOIN contacts ct ON ct.id = c.contact_id
                WHERE c.id = :id AND c.org_id = :o
                """
            ),
            {"id": str(conversation_id), "o": str(org_id)},
        )
    ).mappings().first()
    if head is None:
        return None
    msgs = (
        await session.execute(
            text(
                "SELECT id, direction, body, status, template_key, created_at "
                "FROM messages WHERE conversation_id = :id AND org_id = :o "
                "ORDER BY created_at ASC"
            ),
            {"id": str(conversation_id), "o": str(org_id)},
        )
    ).mappings().all()
    return {**dict(head), "messages": [dict(m) for m in msgs]}


async def list_leads(session: AsyncSession, org_id: UUID) -> list[dict[str, Any]]:
    """The pipeline: leads with their contact + stage (grouped by stage on the client)."""
    await set_org_context(session, org_id)
    rows = (
        await session.execute(
            text(
                """
                SELECT l.id, l.stage, l.source, l.score,
                       l.next_followup_at, l.last_touch_at, l.created_at, l.updated_at,
                       ct.full_name AS contact_name, ct.phone AS contact_phone,
                       -- LEAD-1: where this lead was captured from (any origin)
                       l.landing_page_id, l.variant, l.utm,
                       lp.slug AS landing_slug,
                       ch.type AS channel_type,
                       -- GHOST-1c/1d: the owner's recovery override on this lead
                       l.recovery_state, l.recovery_snooze_until
                FROM leads l
                LEFT JOIN contacts ct ON ct.id = l.contact_id
                LEFT JOIN landing_pages lp ON lp.id = l.landing_page_id
                LEFT JOIN channels ch ON ch.id = l.channel_id
                WHERE l.org_id = :o
                ORDER BY l.updated_at DESC
                LIMIT 200
                """
            ),
            {"o": str(org_id)},
        )
    ).mappings().all()
    return [dict(r) for r in rows]
