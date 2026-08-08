"""Campaigns service (Phase 3.5-eng, Ticket A2.1).

Create/list/get campaign records + `record_execution` (called by the `campaign.executed` consumer
when a send-flow lands). Org-scoped (RLS via `set_org_context` + explicit `org_id` filter).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context

_COLS = ("id, name, channel, audience, template_key, template_lang, status, scheduled_at, "
         "sent_count, failed_count, halt_reason, created_at, executed_at")


async def create_campaign(
    session: AsyncSession, org_id: UUID, *, name: str, channel: str = "whatsapp",
    audience: str | None = None, template_key: str | None = None, template_lang: str = "en",
    scheduled_at: datetime | None = None, created_by: UUID | None = None,
) -> UUID:
    """Create a campaign record. A schedule makes it `scheduled`; otherwise `draft`."""
    await set_org_context(session, org_id)
    status = "scheduled" if scheduled_at else "draft"
    return (
        await session.execute(
            text(
                "INSERT INTO campaigns (org_id, name, channel, audience, template_key, "
                " template_lang, status, scheduled_at, created_by) "
                "VALUES (:o, :n, :ch, :a, :tk, :tl, :st, :sa, :by) RETURNING id"
            ),
            {"o": str(org_id), "n": name, "ch": channel, "a": audience, "tk": template_key,
             "tl": template_lang, "st": status, "sa": scheduled_at,
             "by": str(created_by) if created_by else None},
        )
    ).scalar_one()


async def list_campaigns(session: AsyncSession, org_id: UUID) -> list[dict[str, Any]]:
    await set_org_context(session, org_id)
    rows = (
        await session.execute(
            text(f"SELECT {_COLS} FROM campaigns WHERE org_id = :o "
                 "ORDER BY created_at DESC LIMIT 200"),
            {"o": str(org_id)},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def get_campaign(
    session: AsyncSession, org_id: UUID, campaign_id: UUID
) -> dict[str, Any] | None:
    await set_org_context(session, org_id)
    row = (
        await session.execute(
            text(f"SELECT {_COLS} FROM campaigns WHERE id = :id AND org_id = :o"),
            {"id": str(campaign_id), "o": str(org_id)},
        )
    ).mappings().first()
    return dict(row) if row else None


async def record_execution(
    session: AsyncSession, org_id: UUID, campaign_id: UUID, *, sent: int, failed: int
) -> None:
    """Record a campaign's send counts + mark it executed. Idempotent (sets absolute counts)."""
    await set_org_context(session, org_id)
    await session.execute(
        text(
            "UPDATE campaigns SET sent_count = :s, failed_count = :f, status = 'executed', "
            "executed_at = now(), updated_at = now() WHERE id = :id AND org_id = :o"
        ),
        {"s": sent, "f": failed, "id": str(campaign_id), "o": str(org_id)},
    )
