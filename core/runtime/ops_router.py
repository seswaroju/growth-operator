"""Ops run viewer (MVP-055) — `GET /v1/ops/runs/{id}`.

Owner-level visibility into an agent run: its status, the two audit hashes, and its ordered steps
(the reasoning drawer reads this). Tenant-scoped — RLS confines it to the caller's own org, so a run
from another tenant is a 404, not a leak. (Cross-tenant run inspection for operators is a separate
platform-plane concern; this endpoint is a store owner viewing their own agent — Phase 1.1.)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import ORG_MANAGE
from core.tenancy.rbac import requires

router = APIRouter(prefix="/v1/ops", tags=["ops"])


@router.get("/runs/{run_id}", summary="Agent run + steps (owner)")
async def get_run(
    run_id: UUID,
    current: CurrentAuth = Depends(requires(ORG_MANAGE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    run = (
        await session.execute(
            text(
                "SELECT id, status, trigger, trace_id, composed_prompt_hash, "
                "  permission_manifest_hash, steps_taken, tokens_in, tokens_out, "
                "  output, error, started_at, ended_at "
                "FROM agent_runs WHERE id = :r"
            ),
            {"r": str(run_id)},
        )
    ).mappings().first()
    if run is None:  # RLS-scoped: unknown or another org's run
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    steps = (
        await session.execute(
            text(
                "SELECT seq, node, tool_called, tool_input, tool_output, latency_ms, created_at "
                "FROM agent_steps WHERE run_id = :r ORDER BY seq"
            ),
            {"r": str(run_id)},
        )
    ).mappings().all()
    return {"run": dict(run), "steps": [dict(s) for s in steps]}
