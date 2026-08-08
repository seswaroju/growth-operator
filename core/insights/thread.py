"""Owner⇄Growth-Operator insight thread (Phase 3.5-eng, A4.5).

A per-insight Q&A: the owner asks about a report, the operator answers cross-tenant into the owner's
dashboard. Append-only. Owner paths run org-scoped (`set_org_context`); the operator reply runs on
an admin-scoped session (the `app.platform_admin` flag) and resolves the report's org via the
`resolve_report_org` SECURITY DEFINER helper. The split-RLS then permits an operator-authored insert
into any tenant while an owner may only post an owner-authored row into their own org.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context

_RET = "RETURNING id, author_type, body, created_at"


async def list_thread(
    session: AsyncSession, org_id: UUID, report_id: UUID
) -> list[dict[str, Any]]:
    """The thread for one report, oldest first (owner-side, org-scoped)."""
    await set_org_context(session, org_id)
    rows = (
        await session.execute(
            text("SELECT id, author_type, body, created_at FROM insight_messages "
                 "WHERE org_id = :o AND report_id = :r ORDER BY created_at ASC"),
            {"o": str(org_id), "r": str(report_id)},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def post_owner_message(
    session: AsyncSession, org_id: UUID, report_id: UUID, author_id: UUID | None, body: str
) -> dict[str, Any]:
    """The owner asks a question (org-scoped)."""
    await set_org_context(session, org_id)
    row = (
        await session.execute(
            text("INSERT INTO insight_messages (org_id, report_id, author_type, author_id, body) "
                 f"VALUES (:o, :r, 'owner', :a, :b) {_RET}"),
            {"o": str(org_id), "r": str(report_id),
             "a": str(author_id) if author_id else None, "b": body},
        )
    ).mappings().one()
    return dict(row)


async def resolve_report_org(session: AsyncSession, report_id: UUID) -> UUID | None:
    return (
        await session.execute(
            text("SELECT resolve_report_org(CAST(:r AS uuid))"), {"r": str(report_id)}
        )
    ).scalar_one_or_none()


async def post_operator_message(
    session: AsyncSession, report_id: UUID, operator_id: UUID | None, body: str
) -> tuple[dict[str, Any], UUID] | None:
    """The operator answers cross-tenant (admin session). Returns (message, org_id), or None if the
    report doesn't exist. The split-RLS only admits an `operator`-authored row here."""
    org_id = await resolve_report_org(session, report_id)
    if org_id is None:
        return None
    row = (
        await session.execute(
            text("INSERT INTO insight_messages (org_id, report_id, author_type, author_id, body) "
                 f"VALUES (:o, :r, 'operator', :a, :b) {_RET}"),
            {"o": str(org_id), "r": str(report_id),
             "a": str(operator_id) if operator_id else None, "b": body},
        )
    ).mappings().one()
    return dict(row), org_id
