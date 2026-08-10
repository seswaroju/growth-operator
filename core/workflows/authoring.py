"""Owner-built workflow authoring (MVP-073e, builder backend).

The "server truth" behind the builder UI (client hints, server truth): validate a DSL definition and
save it as an **owner-built** draft, under two guardrails from the spec
(`docs/21-platform/workflow-engine.md` §Builder):

- **owners cannot forge platform events** — an owner-built definition may not contain an `emit` step
  (every event type is platform-owned);
- **complexity budget** — at most `MAX_OWNER_DEFINITIONS` owner-built definitions per tenant.

Platform `mandated_guards` are injected server-side (`OWNER_MANDATED_GUARDS`), so a crafted def can
never ship without them. Owner-built definitions start `draft`; first activation is gated on a
simulation report + tier-2 approval + the trust ledger (MVP-073f).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context
from core.workflows import parser, store
from core.workflows.guards import GuardRef
from core.workflows.parser import ParsedWorkflow

# Injected into every owner-built definition (customer-comms safety floor).
OWNER_MANDATED_GUARDS: list[GuardRef] = [GuardRef("not_suppressed")]
MAX_OWNER_DEFINITIONS = 10


class AuthoringError(ValueError):
    """An owner-built definition violates an authoring guardrail (emit / complexity budget)."""


def _has_emit(steps: list[dict[str, Any]]) -> bool:
    for step in steps:
        if "emit" in step:
            return True
        if "branch" in step:
            if any(_has_emit(c.get("steps", [])) for c in step["branch"].get("cases", [])):
                return True
            if _has_emit(step["branch"].get("default", [])):
                return True
        if "loop" in step and _has_emit(step["loop"].get("steps", [])):
            return True
    return False


def validate_owner_dsl(dsl: dict[str, Any]) -> ParsedWorkflow:
    """Parse + validate a DSL as an owner-built definition (mandated guards injected). Raises
    `WorkflowSchemaError` / `WorkflowParseError` / `UnknownGuard` / `AuthoringError`."""
    parsed = parser.parse(dsl, mandated=OWNER_MANDATED_GUARDS)
    comp = dsl.get("compensation") or {}
    if _has_emit(dsl.get("steps", [])) or _has_emit(comp.get("on_failure", [])):
        raise AuthoringError("owners cannot emit platform events")
    return parsed


async def _owner_count(session: AsyncSession, org_id: UUID) -> int:
    return int((await session.execute(
        text("SELECT count(*) FROM workflow_definitions WHERE org_id = :o "
             "AND origin = 'owner_built'"), {"o": str(org_id)})).scalar_one())


async def create_owner_definition(
    session: AsyncSession, org_id: UUID, dsl: dict[str, Any]
) -> UUID:
    """Validate + save an owner-built **draft**. Enforces the per-tenant complexity budget."""
    parsed = validate_owner_dsl(dsl)
    await set_org_context(session, org_id)
    if await _owner_count(session, org_id) >= MAX_OWNER_DEFINITIONS:
        raise AuthoringError(f"owner workflow limit reached ({MAX_OWNER_DEFINITIONS})")
    return await store.seed_definition(
        session, org_id=org_id, pack_id=None, parsed=parsed,
        origin="owner_built", status="draft")


async def update_owner_definition(
    session: AsyncSession, org_id: UUID, definition_id: UUID, dsl: dict[str, Any]
) -> None:
    """Re-validate + replace an owner-built draft's DSL in place (same row)."""
    parsed = validate_owner_dsl(dsl)
    await set_org_context(session, org_id)
    existing = (await session.execute(
        text("SELECT origin FROM workflow_definitions WHERE id = :d AND org_id = :o"),
        {"d": str(definition_id), "o": str(org_id)})).scalar_one_or_none()
    if existing is None:
        raise KeyError(f"unknown definition {definition_id}")
    if existing != "owner_built":
        raise AuthoringError("only owner-built definitions can be edited")
    import json
    await session.execute(
        text("UPDATE workflow_definitions SET dsl = CAST(:dsl AS jsonb), "
             "trigger_spec = CAST(:trig AS jsonb), guards = CAST(:guards AS jsonb), "
             "updated_at = now() WHERE id = :d AND org_id = :o"),
        {"dsl": json.dumps(parsed.dsl), "trig": json.dumps(parsed.trigger_spec),
         "guards": json.dumps([g.render() for g in parsed.guards]),
         "d": str(definition_id), "o": str(org_id)})


async def list_owner_definitions(
    session: AsyncSession, org_id: UUID
) -> list[dict[str, Any]]:
    """The org's owner-built definitions (for the builder's list view)."""
    await set_org_context(session, org_id)
    rows = (await session.execute(
        text("SELECT id, workflow_key, version, status, created_at, updated_at "
             "FROM workflow_definitions WHERE org_id = :o AND origin = 'owner_built' "
             "ORDER BY created_at DESC"), {"o": str(org_id)})).mappings().all()
    return [dict(r) for r in rows]
