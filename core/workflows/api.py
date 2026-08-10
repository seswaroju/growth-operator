"""Workflow run timeline API (MVP-073c) — the ops viewer's read endpoints.

Tenant-scoped, `insights:read`-gated (operational observability): list the org's recent runs and
read one run's timeline (state + append-only event log). Read-only — runs are driven by the engine,
never mutated here.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import INSIGHTS_READ
from core.tenancy.rbac import requires
from core.workflows import simulate as simulate_mod
from core.workflows import timeline

router = APIRouter(prefix="/v1/workflows", tags=["workflows"])


class SimulateRequest(BaseModel):
    window_days: int = Field(default=30, ge=1, le=180)


@router.get("/runs", summary="List recent workflow runs")
async def list_runs(
    current: CurrentAuth = Depends(requires(INSIGHTS_READ)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no tenant context")
    return {"runs": await timeline.list_runs(session, current.org_id)}


@router.get("/runs/{run_id}", summary="Workflow run timeline (state + events)")
async def get_run(
    run_id: UUID,
    current: CurrentAuth = Depends(requires(INSIGHTS_READ)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no tenant context")
    tl = await timeline.get_run_timeline(session, current.org_id, run_id)
    if tl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return tl


@router.post("/{definition_id}/simulate", summary="Dry-run a definition against tenant history")
async def simulate(
    definition_id: UUID,
    body: SimulateRequest,
    current: CurrentAuth = Depends(requires(INSIGHTS_READ)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no tenant context")
    try:
        return await simulate_mod.simulate(
            session, current.org_id, definition_id, window_days=body.window_days)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "definition not found") from exc
