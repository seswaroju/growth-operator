"""Workflow definition persistence (MVP-072).

Seed a parsed definition into `workflow_definitions` and flip its lifecycle. Pack workflows install
`active`; owner-built definitions (H2, later) would start `draft`. Reads/writes are RLS-scoped — the
caller must already hold tenant context (the pack installer does; `activate` sets it defensively).
"""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context
from core.workflows.parser import ParsedWorkflow


async def seed_definition(
    session: AsyncSession,
    *,
    org_id: UUID,
    pack_id: UUID | None,
    parsed: ParsedWorkflow,
    origin: str = "pack",
    status: str = "active",
) -> UUID:
    """Upsert a parsed definition keyed by (org, workflow_key, version). Reinstalling the same pack
    version overwrites the stored DSL/trigger/guards in place (idempotent). Returns the row id."""
    row = await session.execute(
        text(
            "INSERT INTO workflow_definitions "
            "(org_id, pack_id, workflow_key, version, origin, status, dsl, trigger_spec, guards) "
            "VALUES (:o, :p, :k, :v, :origin, :status, CAST(:dsl AS jsonb), "
            "        CAST(:trig AS jsonb), CAST(:guards AS jsonb)) "
            "ON CONFLICT (org_id, workflow_key, version) DO UPDATE SET "
            "  dsl = EXCLUDED.dsl, trigger_spec = EXCLUDED.trigger_spec, "
            "  guards = EXCLUDED.guards, status = EXCLUDED.status, updated_at = now() "
            "RETURNING id"
        ),
        {"o": str(org_id), "p": str(pack_id) if pack_id else None,
         "k": parsed.workflow_key, "v": parsed.version, "origin": origin, "status": status,
         "dsl": json.dumps(parsed.dsl), "trig": json.dumps(parsed.trigger_spec),
         "guards": json.dumps([g.render() for g in parsed.guards])},
    )
    return row.scalar_one()


async def set_status(
    session: AsyncSession, org_id: UUID, definition_id: UUID, status: str
) -> None:
    await set_org_context(session, org_id)
    await session.execute(
        text("UPDATE workflow_definitions SET status = :s, updated_at = now() "
             "WHERE id = :id AND org_id = :o"),
        {"s": status, "id": str(definition_id), "o": str(org_id)})


async def activate(session: AsyncSession, org_id: UUID, definition_id: UUID) -> None:
    """Internal activation — make a definition eligible for trigger routing (MVP-073)."""
    await set_status(session, org_id, definition_id, "active")


async def deactivate(session: AsyncSession, org_id: UUID, definition_id: UUID) -> None:
    await set_status(session, org_id, definition_id, "disabled")


async def active_definitions_for_event(
    session: AsyncSession, org_id: UUID, event_type: str
) -> list[dict[str, object]]:
    """Active definitions whose trigger fires on `event_type` — the executor's routing lookup."""
    await set_org_context(session, org_id)
    rows = await session.execute(
        text("SELECT id, workflow_key, version, dsl, trigger_spec, guards "
             "FROM workflow_definitions WHERE org_id = :o AND status = 'active' "
             "AND trigger_spec->>'event_type' = :et"),
        {"o": str(org_id), "et": event_type})
    return [dict(r) for r in rows.mappings().all()]
