"""Tracked-competitors service (Phase 3.5-eng, A4.3). Org-scoped (RLS + explicit org filter)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context

_COLS = "id, name, handle, notes, created_at"


async def create_competitor(
    session: AsyncSession, org_id: UUID, *, name: str, handle: str | None = None,
    notes: str | None = None, created_by: UUID | None = None,
) -> UUID:
    await set_org_context(session, org_id)
    return (
        await session.execute(
            text("INSERT INTO tracked_competitors (org_id, name, handle, notes, created_by) "
                 "VALUES (:o, :n, :h, :nt, :by) RETURNING id"),
            {"o": str(org_id), "n": name, "h": handle, "nt": notes,
             "by": str(created_by) if created_by else None},
        )
    ).scalar_one()


async def list_competitors(session: AsyncSession, org_id: UUID) -> list[dict[str, Any]]:
    await set_org_context(session, org_id)
    rows = (
        await session.execute(
            text(f"SELECT {_COLS} FROM tracked_competitors WHERE org_id = :o "
                 "ORDER BY created_at DESC LIMIT 200"),
            {"o": str(org_id)},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def get_competitor(
    session: AsyncSession, org_id: UUID, competitor_id: UUID
) -> dict[str, Any] | None:
    await set_org_context(session, org_id)
    row = (
        await session.execute(
            text(f"SELECT {_COLS} FROM tracked_competitors WHERE id = :id AND org_id = :o"),
            {"id": str(competitor_id), "o": str(org_id)},
        )
    ).mappings().first()
    return dict(row) if row else None


async def delete_competitor(session: AsyncSession, org_id: UUID, competitor_id: UUID) -> bool:
    """Delete one; returns True if a row was removed (False → not the caller's org / not found)."""
    await set_org_context(session, org_id)
    deleted = (
        await session.execute(
            text("DELETE FROM tracked_competitors WHERE id = :id AND org_id = :o RETURNING id"),
            {"id": str(competitor_id), "o": str(org_id)},
        )
    ).scalar_one_or_none()
    return deleted is not None
