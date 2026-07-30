"""Feature flag eval endpoint (MVP-022).

`GET /v1/flags/eval?key=` evaluates a flag for the caller's context. The in-memory snapshot
is refreshed from the DB when older than 30s; the evaluation itself is I/O-free.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy import flags
from core.tenancy.deps import CurrentAuth, get_current_auth
from core.tenancy.middleware import get_db

router = APIRouter(prefix="/v1/flags", tags=["flags"])

SNAPSHOT_TTL_S = 30.0


class FlagEvalResponse(BaseModel):
    key: str
    value: Any
    source: str


@router.get("/eval", response_model=FlagEvalResponse, summary="Evaluate a feature flag")
async def eval_flag(
    key: str = Query(..., min_length=1),
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(get_db),
) -> FlagEvalResponse:
    snap = flags.get_snapshot()
    if time.time() - snap.loaded_at > SNAPSHOT_TTL_S:
        snap = await flags.load_snapshot(session)
        flags.set_snapshot(snap)
    ctx = flags.Ctx(org_id=str(current.org_id), user_id=current.user_id)
    value = flags.eval(snap, key, ctx)
    return FlagEvalResponse(key=key, value=value.value, source=value.source)
