"""Operator LLM-model config for the control plane (CP-5).

The GO operator picks, per store (and per agent-task), which provider+model the runtime uses — from
web-ops. Default is Claude 3.5 Sonnet (the seeded global `model_routes` default); an override is a
row in `org_model_routes` that the runtime's `RoutingModel` consults first. The operator holds the
API keys centrally (decision d1) — this only *selects* a model, never a key.

Under `/v1/admin/tenants/{org_id}/models`:
  GET    — effective config per tunable agent-task (override vs. default).
  PUT    /{node_key} — set an override (validated against the model catalog).
  DELETE /{node_key} — clear the override (revert to the global default).

`org_model_routes` is FORCE-RLS, so reads/writes set the target org's tenant context. Writes are
operator-audited.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.runtime.model_catalog import (
    DEFAULT_CHOICE,
    MODEL_CATALOG,
    TUNABLE_NODES,
    is_tunable_node,
    is_valid_model,
)
from core.tenancy import repository
from core.tenancy.deps import CurrentAuth, get_current_auth
from core.tenancy.platform_admin import (
    log_platform_access,
    require_admin_plane_enabled,
    require_platform,
)
from core.tenancy.platform_permissions import PLATFORM_TENANTS_MANAGE, PLATFORM_TENANTS_READ

router = APIRouter(
    prefix="/v1/admin/tenants",
    tags=["platform"],
    dependencies=[Depends(require_admin_plane_enabled)],
)


class ModelChoiceOut(BaseModel):
    provider: str
    model: str
    label: str


class TunableNodeOut(BaseModel):
    node_key: str
    label: str


class ModelCatalogOut(BaseModel):
    models: list[ModelChoiceOut]
    nodes: list[TunableNodeOut]
    default_provider: str
    default_model: str


class ModelConfigItem(BaseModel):
    node_key: str
    label: str
    provider: str  # effective (override if set, else the global default)
    model: str
    is_override: bool
    default_provider: str  # what it reverts to when the override is cleared
    default_model: str


class ModelOverrideIn(BaseModel):
    provider: str
    model: str


@router.get(
    "/model-catalog", response_model=ModelCatalogOut,
    summary="Provider/model choices + tunable agent-tasks (drives the web-ops picker)")
async def model_catalog(
    _session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> ModelCatalogOut:
    return ModelCatalogOut(
        models=[ModelChoiceOut(provider=c.provider, model=c.model, label=c.label)
                for c in MODEL_CATALOG],
        nodes=[TunableNodeOut(node_key=n.node_key, label=n.label) for n in TUNABLE_NODES],
        default_provider=DEFAULT_CHOICE.provider, default_model=DEFAULT_CHOICE.model)


async def _effective_config(session: AsyncSession, org_id: UUID) -> list[ModelConfigItem]:
    await repository.set_org_context(session, org_id)
    overrides = {
        r["node_key"]: r for r in (
            await session.execute(
                text("SELECT node_key, provider, model FROM org_model_routes"))
        ).mappings()
    }
    globals_ = {
        r["node_key"]: r for r in (
            await session.execute(text("SELECT node_key, provider, model FROM model_routes"))
        ).mappings()
    }
    default_global = globals_.get("default")
    items: list[ModelConfigItem] = []
    for node in TUNABLE_NODES:
        g = globals_.get(node.node_key) or default_global
        default_provider = g["provider"] if g else DEFAULT_CHOICE.provider
        default_model = g["model"] if g else DEFAULT_CHOICE.model
        o = overrides.get(node.node_key)
        items.append(ModelConfigItem(
            node_key=node.node_key, label=node.label,
            provider=o["provider"] if o else default_provider,
            model=o["model"] if o else default_model,
            is_override=o is not None,
            default_provider=default_provider, default_model=default_model))
    return items


@router.get(
    "/{org_id}/models", response_model=list[ModelConfigItem],
    summary="A store's effective model config per agent-task")
async def list_models(
    org_id: UUID,
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_READ)),
) -> list[ModelConfigItem]:
    return await _effective_config(session, org_id)


@router.put(
    "/{org_id}/models/{node_key}", response_model=ModelConfigItem,
    summary="Set a store's model override for one agent-task")
async def set_model(
    org_id: UUID,
    node_key: str,
    body: ModelOverrideIn,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_MANAGE)),
) -> ModelConfigItem:
    if not is_tunable_node(node_key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown agent-task: {node_key}")
    if not is_valid_model(body.provider, body.model):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"unknown model: {body.provider}/{body.model}")
    await repository.set_org_context(session, org_id)
    await session.execute(
        text("INSERT INTO org_model_routes (org_id, node_key, provider, model) "
             "VALUES (:o, :nk, :p, :m) "
             "ON CONFLICT (org_id, node_key) DO UPDATE SET "
             "provider = :p, model = :m, updated_at = now()"),
        {"o": str(org_id), "nk": node_key, "p": body.provider, "m": body.model})
    await log_platform_access(
        session, actor_user_id=current.user_id, action="model.override.set",
        target_org_id=org_id,
        detail={"node_key": node_key, "provider": body.provider, "model": body.model})
    items = await _effective_config(session, org_id)
    return next(i for i in items if i.node_key == node_key)


@router.delete(
    "/{org_id}/models/{node_key}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear a store's model override (revert to the default)")
async def clear_model(
    org_id: UUID,
    node_key: str,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(require_platform(PLATFORM_TENANTS_MANAGE)),
) -> None:
    await repository.set_org_context(session, org_id)
    await session.execute(
        text("DELETE FROM org_model_routes WHERE node_key = :nk"), {"nk": node_key})
    await log_platform_access(
        session, actor_user_id=current.user_id, action="model.override.cleared",
        target_org_id=org_id, detail={"node_key": node_key})
