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
from core.tenancy.permissions import CATALOG_WRITE, INSIGHTS_READ
from core.tenancy.rbac import requires
from core.workflows import activation, authoring, timeline
from core.workflows import simulate as simulate_mod
from core.workflows.guards import UnknownGuard
from core.workflows.parser import WorkflowParseError
from core.workflows.schema import WorkflowSchemaError

router = APIRouter(prefix="/v1/workflows", tags=["workflows"])


class SimulateRequest(BaseModel):
    window_days: int = Field(default=30, ge=1, le=180)


class DefinitionBody(BaseModel):
    dsl: dict[str, object]


_AUTHORING_ERRORS = (
    WorkflowSchemaError, WorkflowParseError, UnknownGuard, authoring.AuthoringError)


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


# ---- Owner-built authoring (builder backend; client hints, server truth) ---------------


@router.post("/definitions/validate", summary="Validate a DSL (server truth for the builder)")
async def validate_definition(
    body: DefinitionBody,
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
) -> dict[str, object]:
    try:
        parsed = authoring.validate_owner_dsl(dict(body.dsl))
    except _AUTHORING_ERRORS as exc:
        return {"valid": False, "error": str(exc)}
    return {"valid": True, "workflow_key": parsed.workflow_key,
            "guards": [g.render() for g in parsed.guards]}


@router.post("/definitions", status_code=status.HTTP_201_CREATED,
             summary="Create an owner-built workflow (draft)")
async def create_definition(
    body: DefinitionBody,
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no tenant context")
    try:
        def_id = await authoring.create_owner_definition(session, current.org_id, dict(body.dsl))
    except _AUTHORING_ERRORS as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await session.commit()
    return {"definition_id": str(def_id), "status": "draft"}


@router.put("/definitions/{definition_id}", summary="Update an owner-built workflow (draft)")
async def update_definition(
    definition_id: UUID,
    body: DefinitionBody,
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no tenant context")
    try:
        await authoring.update_owner_definition(
            session, current.org_id, definition_id, dict(body.dsl))
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "definition not found") from exc
    except _AUTHORING_ERRORS as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await session.commit()
    return {"definition_id": str(definition_id), "updated": True}


@router.get("/definitions", summary="List the org's owner-built workflows")
async def list_definitions(
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no tenant context")
    return {"definitions": await authoring.list_owner_definitions(session, current.org_id)}


@router.post("/definitions/{definition_id}/activate",
             summary="Request activation (simulate + raise a tier-2 approval)")
async def activate_definition(
    definition_id: UUID,
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no tenant context")
    try:
        result = await activation.request_activation(session, current.org_id, definition_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "definition not found") from exc
    except activation.ActivationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await session.commit()
    return result


@router.get("/definitions/{definition_id}/trust", summary="Owner-built trust status")
async def definition_trust(
    definition_id: UUID,
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no tenant context")
    return await activation.owner_trust_status(session, current.org_id, definition_id)
