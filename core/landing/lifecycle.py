"""Landing-page lifecycle + owner approval (LP-2b).

The owner reviews the LP-2a candidate variants and **approves one** (HITL gate #1); the page then
moves through a validated status machine. Every transition is **RLS-scoped, transition-validated,
and audited** (`landing_page.transition`). No transition has an external side effect: `publish`
marks + records only; live public serving is LP-3a, and the agent-initiated path (publish via an
execution-token after an approvals-queue sign-off) is LP-2c. Generic: nothing here names a vertical.

Statuses (migration 045): draft · generated · validated · awaiting_approval · approved · published ·
paused · archived.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.taxonomy import ACTOR_USER, LANDING_PAGE_TRANSITION
from core.audit.writer import AuditEntry
from core.audit.writer import write as audit_write
from core.tenancy.repository import set_org_context

# Allowed status transitions. A transition not listed here is rejected (fail-closed).
_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"generated", "archived"},
    "generated": {"awaiting_approval", "approved", "archived"},
    "validated": {"awaiting_approval", "approved", "archived"},
    "awaiting_approval": {"approved", "generated", "archived"},
    "approved": {"published", "generated", "archived"},
    "published": {"paused", "approved", "archived"},
    "paused": {"published", "approved", "archived"},
    "archived": set(),
}


class InvalidTransition(Exception):
    """A status change that the lifecycle does not allow (API → 409)."""

    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"cannot move a landing page from '{current}' to '{target}'")
        self.current = current
        self.target = target


def can_transition(current: str, target: str) -> bool:
    return target in _TRANSITIONS.get(current, set())


async def _status(session: AsyncSession, page_id: UUID) -> str | None:
    return (
        await session.execute(
            text("SELECT status FROM landing_pages WHERE id = :id"), {"id": str(page_id)})
    ).scalar()


async def _version_id(session: AsyncSession, page_id: UUID, version_no: int) -> UUID | None:
    return (
        await session.execute(
            text("SELECT id FROM landing_page_versions WHERE page_id = :p AND version_no = :n"),
            {"p": str(page_id), "n": version_no})
    ).scalar()


async def _audit(
    session: AsyncSession, org_id: UUID, page_id: UUID, actor_id: UUID | None,
    frm: str, to: str, version_no: int | None = None,
) -> None:
    payload: dict[str, object] = {"page_id": str(page_id), "from": frm, "to": to}
    if version_no is not None:
        payload["version_no"] = version_no
    await audit_write(session, AuditEntry(
        org_id=org_id, actor_type=ACTOR_USER, actor_id=str(actor_id) if actor_id else None,
        action=LANDING_PAGE_TRANSITION, resource=str(page_id), payload=payload))


async def _transition(
    session: AsyncSession, org_id: UUID, page_id: UUID, target: str, actor_id: UUID | None,
    *, extra_sql: str = "", params: dict[str, object] | None = None, version_no: int | None = None,
) -> str | None:
    """Validate current→target, apply status (+ optional extra columns), audit. None → 404 page."""
    await set_org_context(session, org_id)
    current = await _status(session, page_id)
    if current is None:
        return None  # unknown / other-org → 404 (RLS-scoped)
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
    await session.execute(
        text(f"UPDATE landing_pages SET status = :s{extra_sql} WHERE id = :p"),
        {"s": target, "p": str(page_id), **(params or {})})
    await _audit(session, org_id, page_id, actor_id, current, target, version_no)
    return target


async def submit_for_approval(
    session: AsyncSession, org_id: UUID, page_id: UUID, actor_id: UUID | None = None
) -> str | None:
    return await _transition(session, org_id, page_id, "awaiting_approval", actor_id)


async def select_variant(
    session: AsyncSession, org_id: UUID, page_id: UUID, version_no: int,
    actor_id: UUID | None = None,
) -> str | None:
    """Owner approves + picks a candidate (HITL #1): → `approved`, current version + approver set.

    Returns the new status, `None` if the page/version is not found (→404), or raises
    `InvalidTransition` (→409)."""
    await set_org_context(session, org_id)
    version_id = await _version_id(session, page_id, version_no)
    if version_id is None:
        return None  # unknown page or version → 404
    new_status = await _transition(
        session, org_id, page_id, "approved", actor_id,
        extra_sql=", current_version_id = :v", params={"v": str(version_id)},
        version_no=version_no)
    # `approved_by` lives on the version (it records WHO approved WHICH candidate).
    await session.execute(
        text("UPDATE landing_page_versions SET approved_by = :by WHERE id = :v"),
        {"by": str(actor_id) if actor_id else None, "v": str(version_id)})
    return new_status


async def publish(
    session: AsyncSession, org_id: UUID, page_id: UUID, actor_id: UUID | None = None
) -> str | None:
    """Mark the approved page published (record only — live serving is LP-3a)."""
    new_status = await _transition(session, org_id, page_id, "published", actor_id)
    # `published_at` lives on the version that is now live.
    await session.execute(
        text("UPDATE landing_page_versions SET published_at = now() "
             "WHERE id = (SELECT current_version_id FROM landing_pages WHERE id = :p)"),
        {"p": str(page_id)})
    return new_status


async def pause(
    session: AsyncSession, org_id: UUID, page_id: UUID, actor_id: UUID | None = None
) -> str | None:
    return await _transition(session, org_id, page_id, "paused", actor_id)


async def archive(
    session: AsyncSession, org_id: UUID, page_id: UUID, actor_id: UUID | None = None
) -> str | None:
    return await _transition(session, org_id, page_id, "archived", actor_id)


async def rollback(
    session: AsyncSession, org_id: UUID, page_id: UUID, version_no: int,
    actor_id: UUID | None = None,
) -> str | None:
    """Repoint the current version to an earlier candidate → `approved` (needs re-publish)."""
    await set_org_context(session, org_id)
    version_id = await _version_id(session, page_id, version_no)
    if version_id is None:
        return None
    return await _transition(
        session, org_id, page_id, "approved", actor_id,
        extra_sql=", current_version_id = :v", params={"v": str(version_id)},
        version_no=version_no)
