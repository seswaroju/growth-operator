"""Notification feed (MVP-075) — the owner bell.

Derives a unified feed from signals that already exist rather than a new event pipeline: **pending
approvals** (the owner must act — a customer reply won't send until they do), **support-ticket
updates** (an operator moved/resolved their ticket), and **automation alerts** (a workflow run that
failed or compensated). Each item carries a `kind`, a title, and its timestamp `at`.

Unread = items newer than the user's `seen_at` (one row per user in `notification_reads`); opening
bell marks everything seen. All reads are RLS-scoped, so a user only ever sees their own org.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context

_PER_SOURCE = 15  # cap per source before merging


async def _seen_at(session: AsyncSession, org_id: UUID, user_id: UUID) -> datetime | None:
    return (await session.execute(
        text("SELECT seen_at FROM notification_reads WHERE org_id = :o AND user_id = :u"),
        {"o": str(org_id), "u": str(user_id)})).scalar_one_or_none()


async def get_feed(
    session: AsyncSession, org_id: UUID, user_id: UUID, *, limit: int = 30
) -> dict[str, Any]:
    """The user's notification feed (newest first) + the unread count."""
    await set_org_context(session, org_id)
    seen = await _seen_at(session, org_id, user_id)
    items: list[dict[str, Any]] = []

    for r in (await session.execute(
        text("SELECT id, action_type, tier, created_at FROM approvals "
             "WHERE org_id = :o AND status = 'pending' ORDER BY created_at DESC LIMIT :n"),
        {"o": str(org_id), "n": _PER_SOURCE})).mappings():
        items.append({"kind": "approval", "ref": str(r["id"]),
                      "title": f"Approval needed: {r['action_type']}",
                      "tier": r["tier"], "at": r["created_at"]})

    for r in (await session.execute(
        text("SELECT id, subject, status, updated_at FROM support_tickets "
             "WHERE org_id = :o AND status IN ('in_progress','resolved') "
             "ORDER BY updated_at DESC LIMIT :n"),
        {"o": str(org_id), "n": _PER_SOURCE})).mappings():
        items.append({"kind": "ticket", "ref": str(r["id"]),
                      "title": f"Ticket {r['status'].replace('_', ' ')}: {r['subject']}",
                      "at": r["updated_at"]})

    for r in (await session.execute(
        text("SELECT r.id, d.workflow_key, r.status, r.updated_at FROM workflow_runs r "
             "JOIN workflow_definitions d ON d.id = r.definition_id "
             "WHERE r.org_id = :o AND r.status IN ('failed','compensated','compensated_partial') "
             "ORDER BY r.updated_at DESC LIMIT :n"),
        {"o": str(org_id), "n": _PER_SOURCE})).mappings():
        items.append({"kind": "automation", "ref": str(r["id"]),
                      "title": f"Automation ‘{r['workflow_key']}’ {r['status']}",
                      "at": r["updated_at"]})

    # Operator broadcasts (CP-7): global GO→all-stores announcements. `announcements` has no RLS, so
    # every store's owner sees the active ones — that's the "blast to all stores".
    for r in (await session.execute(
        text("SELECT id, title, body, level, published_at FROM announcements "
             "WHERE archived_at IS NULL ORDER BY published_at DESC LIMIT :n"),
        {"n": _PER_SOURCE})).mappings():
        items.append({"kind": "announcement", "ref": str(r["id"]),
                      "title": r["title"], "body": r["body"], "level": r["level"],
                      "at": r["published_at"]})

    items.sort(key=lambda i: i["at"], reverse=True)
    items = items[:limit]
    unread = sum(1 for i in items if seen is None or i["at"] > seen)
    return {"items": items, "unread_count": unread, "seen_at": seen}


async def mark_seen(session: AsyncSession, org_id: UUID, user_id: UUID) -> None:
    """Record that the user opened the bell (upsert `seen_at = now`)."""
    await set_org_context(session, org_id)
    await session.execute(
        text("INSERT INTO notification_reads (org_id, user_id, seen_at) VALUES (:o, :u, now()) "
             "ON CONFLICT (org_id, user_id) DO UPDATE SET seen_at = now()"),
        {"o": str(org_id), "u": str(user_id)})


# ---- Operator broadcasts / announcements (CP-7) --------------------------------------------------
# `announcements` is a GLOBAL GO→all-stores table (no RLS): the operator writes; every store's owner
# reads the active rows through `get_feed`. Only operator-plane routes reach these writers.

_ANNOUNCEMENT_COLS = "id, title, body, level, published_at, archived_at, created_at"


async def create_announcement(
    session: AsyncSession, *, title: str, body: str, level: str, created_by: UUID
) -> dict[str, Any]:
    """Publish a broadcast (create = publish; `archived_at` retracts it later)."""
    return dict((await session.execute(
        text(f"INSERT INTO announcements (title, body, level, created_by) "
             f"VALUES (:t, :b, :l, :c) RETURNING {_ANNOUNCEMENT_COLS}"),
        {"t": title, "b": body, "l": level, "c": str(created_by)})).mappings().one())


async def list_announcements(session: AsyncSession) -> list[dict[str, Any]]:
    """All broadcasts (active + archived), newest first — the operator's management list."""
    rows = (await session.execute(
        text(f"SELECT {_ANNOUNCEMENT_COLS} FROM announcements ORDER BY published_at DESC")
    )).mappings().all()
    return [dict(r) for r in rows]


async def archive_announcement(session: AsyncSession, announcement_id: UUID) -> bool:
    """Retract a broadcast (drops it from every owner feed). False if unknown/already archived."""
    return (await session.execute(
        text("UPDATE announcements SET archived_at = now() "
             "WHERE id = :id AND archived_at IS NULL RETURNING id"),
        {"id": str(announcement_id)})).first() is not None
