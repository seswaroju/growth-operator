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
