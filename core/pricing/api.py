"""Pricing HTTP routes (MVP-052).

`POST /v1/pricing/compute` computes a quote (writing quote + ledger rows atomically),
`POST /v1/pricing/replay` reports a byte-exact recompute from provenance, and
`GET /v1/rates/status` reports per-source rate freshness. The agent tool uses the service
functions directly (in-process), not these endpoints.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.pricing import rates, service
from core.pricing.functions import PricingError
from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import get_db
from core.tenancy.permissions import CATALOG_READ, ORG_MANAGE
from core.tenancy.rbac import requires

router = APIRouter(prefix="/v1/pricing", tags=["pricing"])
rates_router = APIRouter(prefix="/v1/rates", tags=["pricing"])


class ComputeRequest(BaseModel):
    strategy: str = Field(..., min_length=1)
    inputs: dict[str, Any]
    params: dict[str, Any]
    lead_id: UUID | None = None
    conversation_id: UUID | None = None
    valid_hours: int = Field(default=24, ge=1, le=168)


class QuoteResponse(BaseModel):
    quote_id: UUID


class ReplayRequest(BaseModel):
    quote_id: UUID


class ReplayResponse(BaseModel):
    quote_id: UUID
    matches: bool
    stored_total: int
    recomputed_total: int


@router.post("/compute", response_model=QuoteResponse, summary="Compute a quote + ledger")
async def compute(
    body: ComputeRequest,
    current: CurrentAuth = Depends(requires(CATALOG_READ)),
    session: AsyncSession = Depends(get_db),
) -> QuoteResponse:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        quote_id = await service.compute_quote(
            session, current.org_id, strategy_key=body.strategy, inputs=body.inputs,
            params=body.params, lead_id=body.lead_id, conversation_id=body.conversation_id,
            valid_hours=body.valid_hours,
        )
    except PricingError as exc:
        code = (
            status.HTTP_409_CONFLICT if exc.code == "stale_rate"
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        raise HTTPException(code, detail={"code": exc.code, "detail": str(exc)}) from exc
    return QuoteResponse(quote_id=quote_id)


@router.post("/replay", response_model=ReplayResponse, summary="Replay a quote (byte-match report)")
async def replay(
    body: ReplayRequest,
    current: CurrentAuth = Depends(requires(CATALOG_READ)),
    session: AsyncSession = Depends(get_db),
) -> ReplayResponse:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        report = await service.replay_quote(session, current.org_id, body.quote_id)
    except PricingError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return ReplayResponse(
        quote_id=report.quote_id, matches=report.matches,
        stored_total=report.stored_total, recomputed_total=report.recomputed_total,
    )


class ManualRateRequest(BaseModel):
    source: str = Field(..., min_length=1)
    value: dict[str, int] = Field(..., min_length=1)


class ManualRateResponse(BaseModel):
    snapshot_id: UUID


@rates_router.post("/manual", response_model=ManualRateResponse, summary="Owner manual rate entry")
async def manual_rate(
    body: ManualRateRequest,
    current: CurrentAuth = Depends(requires(ORG_MANAGE)),
    session: AsyncSession = Depends(get_db),
) -> ManualRateResponse:
    """The launch hedge: an owner enters a rate directly (audited, fresh for the staleness window).
    Real tier-2 approval-workflow gating awaits the approvals engine (MVP-065)."""
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    try:
        snapshot_id = await rates.record_manual_rate(
            session, body.source, body.value, org_id=current.org_id, actor_id=current.user_id
        )
    except PricingError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "detail": str(exc)},
        ) from exc
    return ManualRateResponse(snapshot_id=snapshot_id)


@rates_router.get("/status", summary="Per-source rate freshness")
async def rates_status(
    current: CurrentAuth = Depends(requires(CATALOG_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no org context")
    pack_id = (
        await session.execute(
            text(
                "SELECT pack_id FROM pack_installations WHERE org_id = :o AND status = 'active' "
                "ORDER BY priority LIMIT 1"
            ),
            {"o": str(current.org_id)},
        )
    ).scalar_one_or_none()
    if pack_id is None:
        return []
    return await service.rates_status(session, pack_id)
