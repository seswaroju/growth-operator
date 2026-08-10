"""Workflow run timeline (MVP-073c) — the read side of the ops viewer.

`get_run_timeline` returns a run plus its append-only event log in order; `list_runs` gives the
recent runs for an org. Both are tenant-scoped (RLS) — an owner/operator sees only their own runs.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context


async def get_run_timeline(
    session: AsyncSession, org_id: UUID, run_id: UUID
) -> dict[str, Any] | None:
    """A run's current state + its ordered event log, or None if it isn't this org's run."""
    await set_org_context(session, org_id)
    run = (await session.execute(
        text("SELECT r.id, r.definition_id, d.workflow_key, r.definition_version, r.status, "
             "r.cursor, r.concurrency_key, r.subject, r.vars, r.created_at, r.updated_at, "
             "r.completed_at FROM workflow_runs r "
             "JOIN workflow_definitions d ON d.id = r.definition_id "
             "WHERE r.id = :r AND r.org_id = :o"),
        {"r": str(run_id), "o": str(org_id)})).mappings().first()
    if run is None:
        return None
    events = (await session.execute(
        text("SELECT seq, kind, step_id, data, created_at FROM workflow_run_events "
             "WHERE run_id = :r ORDER BY seq"),
        {"r": str(run_id)})).mappings().all()
    return {"run": dict(run), "events": [dict(e) for e in events]}


async def list_runs(
    session: AsyncSession, org_id: UUID, *, limit: int = 50
) -> list[dict[str, Any]]:
    """Recent runs for the org (newest first) — the timeline list view."""
    await set_org_context(session, org_id)
    rows = (await session.execute(
        text("SELECT r.id, d.workflow_key, r.status, r.definition_version, r.created_at, "
             "r.updated_at FROM workflow_runs r "
             "JOIN workflow_definitions d ON d.id = r.definition_id "
             "WHERE r.org_id = :o ORDER BY r.created_at DESC LIMIT :n"),
        {"o": str(org_id), "n": limit})).mappings().all()
    return [dict(r) for r in rows]
