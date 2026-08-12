"""Support-ticket domain logic (support-tickets track).

Two audiences share one table under different RLS scopes:
- **owner** functions (`raise_ticket`, `list_own`, `get_own`) run inside a normal org-scoped session
  (`get_db`), so RLS confines them to the caller's tenant. INSERT is org-only by policy, so an owner
  can never file under another org.
- **operator** functions (`list_all`, `update_ticket`) run inside the audited cross-tenant session
  (`get_admin_db`, `app.platform_admin='on'`), so they see/act across tenants. Every operator change
  is written to the affected tenant's audit chain.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.writer import AuditEntry
from core.audit.writer import write as audit_write
from core.events.outbox import emit
from core.tenancy.platform_admin import log_platform_access

QUEUE_VIEWED_ACTION = "support.queue.viewed"

PRIORITIES = frozenset({"low", "normal", "high", "urgent"})
SEVERITIES = frozenset({"minor", "major", "critical"})
STATUSES = frozenset({"open", "in_progress", "resolved", "closed"})
_CLOSED_STATES = frozenset({"resolved", "closed"})

TICKET_UPDATED_ACTION = "support.ticket.updated"

# Columns returned to the owner view (no cross-tenant fields).
_OWNER_COLS = (
    "id, subject, description, category, priority, severity, status, resolution_note, "
    "created_at, updated_at, resolved_at"
)

# Operator view — same fields plus the tenant the ticket belongs to.
_ADMIN_SELECT = (
    "SELECT t.id, t.org_id, o.name AS org_name, t.raised_by, t.subject, t.description, "
    " t.category, t.priority, t.severity, t.status, t.resolution_note, t.created_at, "
    " t.updated_at, t.resolved_at "
    "FROM support_tickets t JOIN organizations o ON o.id = t.org_id"
)


class SupportError(Exception):
    """Base for support-ticket problems."""


class InvalidField(SupportError):
    """A priority/severity/status value outside its allowed set."""


class TicketNotFound(SupportError):
    """No ticket with that id is visible in the current scope."""


def _priority_rank_sql(col: str = "priority") -> str:
    return (
        f"CASE {col} WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
        f"WHEN 'normal' THEN 2 ELSE 3 END"
    )


async def raise_ticket(
    session: AsyncSession,
    org_id: UUID,
    *,
    subject: str,
    description: str,
    category: str = "other",
    severity: str = "minor",
    raised_by: UUID | None = None,
) -> dict[str, Any]:
    """Store owner files a ticket in their own org (priority defaults to 'normal' for the operator
    to triage; status starts 'open'). Runs under the caller's org-scoped session (RLS)."""
    if severity not in SEVERITIES:
        raise InvalidField(f"severity must be one of {sorted(SEVERITIES)}")
    row = (
        await session.execute(
            text(
                f"INSERT INTO support_tickets (org_id, raised_by, subject, description, category, "
                f" severity) VALUES (:org, :by, :subj, :desc, :cat, :sev) RETURNING {_OWNER_COLS}"
            ),
            {"org": str(org_id), "by": str(raised_by) if raised_by else None,
             "subj": subject, "desc": description, "cat": category, "sev": severity},
        )
    ).mappings().one()
    # Notify the operator plane in the same txn (#21) — the queue also polls, so this is additive.
    await emit(
        session, org_id=org_id, event_type="support.ticket.raised.v1", source="support",
        payload={"ticket_id": str(row["id"]), "priority": row["priority"],
                 "severity": row["severity"]})
    return dict(row)


async def list_own(
    session: AsyncSession, org_id: UUID, *, status: str | None = None
) -> list[dict[str, Any]]:
    """The caller's own tickets (RLS already confines to their org; org_id kept explicit for
    clarity). Newest first."""
    clause = " AND status = :st" if status else ""
    rows = (
        await session.execute(
            text(
                f"SELECT {_OWNER_COLS} FROM support_tickets "
                f"WHERE org_id = :org{clause} ORDER BY created_at DESC"
            ),
            {"org": str(org_id), **({"st": status} if status else {})},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def get_own(
    session: AsyncSession, org_id: UUID, ticket_id: UUID
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(f"SELECT {_OWNER_COLS} FROM support_tickets WHERE id = :id AND org_id = :org"),
            {"id": str(ticket_id), "org": str(org_id)},
        )
    ).mappings().first()
    return dict(row) if row else None


async def list_all(
    session: AsyncSession, *, actor_user_id: UUID, status: str | None = None,
    priority: str | None = None,
) -> list[dict[str, Any]]:
    """Operator queue across ALL tenants (requires the platform-admin session). Ordered urgent→low
    then newest. Joins the org name so the operator sees which store raised it. Every call is
    recorded to the append-only platform access log — cross-tenant *reads* are audited, not just
    writes."""
    where = []
    params: dict[str, Any] = {}
    if status:
        where.append("t.status = :st")
        params["st"] = status
    if priority:
        where.append("t.priority = :pr")
        params["pr"] = priority
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = (
        await session.execute(
            text(f"{_ADMIN_SELECT}{clause} "
                 f"ORDER BY {_priority_rank_sql('t.priority')}, t.created_at DESC"),
            params,
        )
    ).mappings().all()
    result = [dict(r) for r in rows]
    await log_platform_access(
        session, actor_user_id=actor_user_id, action=QUEUE_VIEWED_ACTION,
        detail={"count": len(result), "status": status, "priority": priority},
    )
    return result


async def get_admin(session: AsyncSession, ticket_id: UUID) -> dict[str, Any] | None:
    """One ticket in the operator view (requires the platform-admin session)."""
    row = (
        await session.execute(
            text(f"{_ADMIN_SELECT} WHERE t.id = :id"), {"id": str(ticket_id)}
        )
    ).mappings().first()
    return dict(row) if row else None


async def update_ticket(
    session: AsyncSession,
    ticket_id: UUID,
    *,
    actor_id: UUID,
    priority: str | None = None,
    status: str | None = None,
    resolution_note: str | None = None,
) -> dict[str, Any]:
    """Operator triages/resolves a ticket (cross-tenant session). Sets resolved_at/resolved_by when
    moving to a closed state; writes the change to the affected tenant's audit chain. Raises
    TicketNotFound if the id isn't visible."""
    if priority is not None and priority not in PRIORITIES:
        raise InvalidField(f"priority must be one of {sorted(PRIORITIES)}")
    if status is not None and status not in STATUSES:
        raise InvalidField(f"status must be one of {sorted(STATUSES)}")

    sets = ["updated_at = now()"]
    params: dict[str, Any] = {"id": str(ticket_id)}
    if priority is not None:
        sets.append("priority = :pr")
        params["pr"] = priority
    if status is not None:
        sets.append("status = :st")
        params["st"] = status
        # entering a closed state stamps who/when; reopening clears it
        if status in _CLOSED_STATES:
            sets.append("resolved_at = now()")
            sets.append("resolved_by = :actor")
            params["actor"] = str(actor_id)
        else:
            sets.append("resolved_at = NULL")
            sets.append("resolved_by = NULL")
    if resolution_note is not None:
        sets.append("resolution_note = :note")
        params["note"] = resolution_note

    row = (
        await session.execute(
            text(
                f"UPDATE support_tickets SET {', '.join(sets)} WHERE id = :id "
                "RETURNING id, org_id, subject, priority, severity, status, resolution_note"
            ),
            params,
        )
    ).mappings().first()
    if row is None:
        raise TicketNotFound(str(ticket_id))

    changes = {k: v for k, v in
               {"priority": priority, "status": status,
                "resolution_note": resolution_note}.items() if v is not None}
    await audit_write(
        session,
        AuditEntry(
            org_id=row["org_id"], actor_type="platform_admin", actor_id=str(actor_id),
            action=TICKET_UPDATED_ACTION, resource=str(ticket_id), payload={"changes": changes},
        ),
    )
    # Also record the write on the admin plane (the operator's own cross-tenant activity trail).
    await log_platform_access(
        session, actor_user_id=actor_id, action=TICKET_UPDATED_ACTION,
        target_org_id=row["org_id"], detail={"ticket_id": str(ticket_id), "changes": changes},
    )
    return dict(row)
