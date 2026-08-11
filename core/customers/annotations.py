"""Customer notes + tags (CRM depth, D2).

Small write-model over `customer_notes` + `contact_tags`: an owner/manager attaches free-text notes
and short labels to a contact. Every op verifies the contact is the caller's org first (returns
`None` → 404 rather than trusting a supplied id), and is org-scoped two ways (RLS `set_org_context`
+ explicit `org_id`), so one org can never annotate — or read the annotations of — another's.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context


async def _owns_contact(session: AsyncSession, org_id: UUID, contact_id: UUID) -> bool:
    row = (
        await session.execute(
            text("SELECT 1 FROM contacts WHERE id = :c AND org_id = :o"),
            {"c": str(contact_id), "o": str(org_id)},
        )
    ).first()
    return row is not None


async def add_note(
    session: AsyncSession, org_id: UUID, contact_id: UUID, *, author_user_id: UUID | None, body: str
) -> dict[str, Any] | None:
    """Append a note to a contact. Returns the created note, or `None` if not the org's contact."""
    await set_org_context(session, org_id)
    if not await _owns_contact(session, org_id, contact_id):
        return None
    row = (
        await session.execute(
            text(
                "INSERT INTO customer_notes (org_id, contact_id, author_user_id, body) "
                "VALUES (:o, :c, :a, :b) RETURNING id, author_user_id, body, created_at"
            ),
            {"o": str(org_id), "c": str(contact_id),
             "a": str(author_user_id) if author_user_id else None, "b": body},
        )
    ).mappings().one()
    return dict(row)


async def list_notes(
    session: AsyncSession, org_id: UUID, contact_id: UUID
) -> list[dict[str, Any]] | None:
    """A contact's notes, newest first. `None` if not the org's contact."""
    await set_org_context(session, org_id)
    if not await _owns_contact(session, org_id, contact_id):
        return None
    rows = (
        await session.execute(
            text(
                "SELECT id, author_user_id, body, created_at FROM customer_notes "
                "WHERE org_id = :o AND contact_id = :c ORDER BY created_at DESC"
            ),
            {"o": str(org_id), "c": str(contact_id)},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def add_tag(
    session: AsyncSession, org_id: UUID, contact_id: UUID, *, tag: str, created_by: UUID | None
) -> bool | None:
    """Attach a tag (idempotent). Returns True if newly added, False if it already existed, or
    `None` if not the org's contact."""
    await set_org_context(session, org_id)
    if not await _owns_contact(session, org_id, contact_id):
        return None
    row = (
        await session.execute(
            text(
                "INSERT INTO contact_tags (org_id, contact_id, tag, created_by) "
                "VALUES (:o, :c, :t, :b) ON CONFLICT (org_id, contact_id, tag) DO NOTHING "
                "RETURNING tag"
            ),
            {"o": str(org_id), "c": str(contact_id), "t": tag,
             "b": str(created_by) if created_by else None},
        )
    ).first()
    return row is not None


async def remove_tag(
    session: AsyncSession, org_id: UUID, contact_id: UUID, *, tag: str
) -> bool | None:
    """Remove a tag. Returns True if a row was deleted, False if absent, `None` if not the org's."""
    await set_org_context(session, org_id)
    if not await _owns_contact(session, org_id, contact_id):
        return None
    row = (
        await session.execute(
            text(
                "DELETE FROM contact_tags WHERE org_id = :o AND contact_id = :c AND tag = :t "
                "RETURNING tag"
            ),
            {"o": str(org_id), "c": str(contact_id), "t": tag},
        )
    ).first()
    return row is not None


async def list_tags(
    session: AsyncSession, org_id: UUID, contact_id: UUID
) -> list[str] | None:
    """A contact's tags, alphabetical. `None` if not the org's contact."""
    await set_org_context(session, org_id)
    if not await _owns_contact(session, org_id, contact_id):
        return None
    rows = (
        await session.execute(
            text(
                "SELECT tag FROM contact_tags WHERE org_id = :o AND contact_id = :c ORDER BY tag"
            ),
            {"o": str(org_id), "c": str(contact_id)},
        )
    ).scalars().all()
    return list(rows)
