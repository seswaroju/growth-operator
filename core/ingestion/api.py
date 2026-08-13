"""Imports HTTP routes + SSE relay (MVP-076).

`POST /v1/imports` creates an onboarding batch from a multipart upload (photos / CSV / xlsx),
enforcing the per-batch caps; the batch then moves through the ingestion state machine. The wizard
watches progress on `GET /v1/imports/{id}/stream` — a Server-Sent-Events relay of the batch's
`import.batch_state` events. Extraction / review / load land in MVP-077–080.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.config import get_settings
from core.ingestion import service
from core.ingestion.state import BatchState, is_terminal
from core.tenancy.deps import CurrentAuth
from core.tenancy.entitlements import CATALOG_INGESTION, requires_feature
from core.tenancy.middleware import get_db
from core.tenancy.permissions import CATALOG_READ, CATALOG_WRITE
from core.tenancy.rbac import requires

router = APIRouter(prefix="/v1/imports", tags=["imports"])

_STREAM = "gop:events:import.batch_state.v1"


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create an import batch (upload)",
             dependencies=[Depends(requires_feature(CATALOG_INGESTION))])
async def create_import(
    source_kind: str = Form(...),
    files: list[UploadFile] = File(...),
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no organization context")
    data = b"".join([await f.read() for f in files])
    image_count = len(files) if source_kind == "photo" else 0
    try:
        batch = await service.create_batch(
            session, current.org_id, source_kind=source_kind,
            filename=files[0].filename if files else None, data=data,
            image_count=image_count, created_by=current.user_id,
        )
    except service.CapExceeded as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "cap_exceeded", "detail": exc.detail, "hint": exc.hint},
        ) from exc
    return {"batch_id": str(batch["batch_id"]), "state": batch["state"],
            "source_kind": source_kind, "stats": batch["stats"]}


@router.get("", summary="List import batches")
async def list_imports(
    current: CurrentAuth = Depends(requires(CATALOG_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no organization context")
    return await service.list_batches(session, current.org_id)


@router.get("/{batch_id}", summary="Get one import batch")
async def get_import(
    batch_id: UUID,
    current: CurrentAuth = Depends(requires(CATALOG_READ)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no organization context")
    batch = await service.get_batch(session, current.org_id, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown import batch")
    return batch


@router.post("/{batch_id}/extract", summary="Run CSV/XLSX extraction + column mapping (MVP-078)",
             dependencies=[Depends(requires_feature(CATALOG_INGESTION))])
async def extract_import(
    batch_id: UUID,
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no organization context")
    from core.ingestion import extract_csv, extract_photo
    from core.ingestion.state import IllegalTransition

    batch = await service.get_batch(session, current.org_id, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown import batch")
    try:
        if batch["source_kind"] == "photo":  # gated-simulated vision (MVP-077)
            rows = await extract_photo.extract_photos(session, current.org_id, batch_id)
        else:  # CSV / XLSX (MVP-078)
            rows = await extract_csv.extract_batch(session, current.org_id, batch_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown import batch") from exc
    except IllegalTransition as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "batch is not in an extractable state") from exc
    except extract_csv.ExtractionFailed as exc:
        # the batch is now 'failed' (resumable); return normally so get_db commits that state
        return {"batch_id": str(batch_id), "state": "failed", "error": str(exc)}
    return {"batch_id": str(batch_id), "state": "extracted", "rows": rows}


@router.post("/{batch_id}/validate", summary="Validate + flag rows, move to review (MVP-079)",
             dependencies=[Depends(requires_feature(CATALOG_INGESTION))])
async def validate_import(
    batch_id: UUID,
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no organization context")
    from core.ingestion import review
    from core.ingestion.state import IllegalTransition

    try:
        return await review.validate_batch(session, current.org_id, batch_id)
    except IllegalTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "batch is not ready to validate") from exc


class RowEdit(BaseModel):
    normalized: dict[str, Any]


async def _row_action(action: str, org_id: UUID, batch_id: UUID, seq: int,
                      session: AsyncSession, body: RowEdit | None = None) -> dict[str, Any]:
    from core.ingestion import review

    try:
        if action == "confirm":
            await review.confirm_row(session, org_id, batch_id, seq)
        elif action == "reject":
            await review.reject_row(session, org_id, batch_id, seq)
        elif action == "edit" and body is not None:
            await review.edit_row(session, org_id, batch_id, seq, body.normalized)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return await review.review_summary(session, org_id, batch_id)


@router.post("/{batch_id}/rows/{seq}/confirm", summary="Confirm a row",
             dependencies=[Depends(requires_feature(CATALOG_INGESTION))])
async def confirm_row_ep(
    batch_id: UUID, seq: int,
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no organization context")
    return await _row_action("confirm", current.org_id, batch_id, seq, session)


@router.post("/{batch_id}/rows/{seq}/reject", summary="Reject a row (won't load)",
             dependencies=[Depends(requires_feature(CATALOG_INGESTION))])
async def reject_row_ep(
    batch_id: UUID, seq: int,
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no organization context")
    return await _row_action("reject", current.org_id, batch_id, seq, session)


@router.patch("/{batch_id}/rows/{seq}", summary="Edit a row's fields then confirm",
             dependencies=[Depends(requires_feature(CATALOG_INGESTION))])
async def edit_row_ep(
    batch_id: UUID, seq: int, body: RowEdit,
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no organization context")
    return await _row_action("edit", current.org_id, batch_id, seq, session, body)


@router.post("/{batch_id}/rows/confirm-all", summary="Bulk-confirm rows (auto = confidence gate)",
             dependencies=[Depends(requires_feature(CATALOG_INGESTION))])
async def confirm_all_ep(
    batch_id: UUID, auto: bool = False,
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no organization context")
    from core.ingestion import review

    try:
        return await review.confirm_all(session, current.org_id, batch_id, auto=auto)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.post("/{batch_id}/load", summary="Load confirmed rows into the catalog (MVP-080)",
             dependencies=[Depends(requires_feature(CATALOG_INGESTION))])
async def load_import(
    batch_id: UUID,
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no organization context")
    from core.ingestion import load
    from core.ingestion.state import IllegalTransition

    try:
        result = await load.load_batch(session, current.org_id, batch_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown import batch") from exc
    except IllegalTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "batch is not ready to load") from exc
    return {"batch_id": str(batch_id), "state": "loaded", **result}


@router.post("/{batch_id}/revert", summary="Revert a load within 30 days (MVP-080)",
             dependencies=[Depends(requires_feature(CATALOG_INGESTION))])
async def revert_import(
    batch_id: UUID,
    current: CurrentAuth = Depends(requires(CATALOG_WRITE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no organization context")
    from core.ingestion import load

    try:
        result = await load.revert_batch(session, current.org_id, batch_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown import batch") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return {"batch_id": str(batch_id), "state": "reverted", **result}


@router.get("/{batch_id}/rows", summary="List a batch's extracted rows")
async def list_import_rows(
    batch_id: UUID,
    current: CurrentAuth = Depends(requires(CATALOG_READ)),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no organization context")
    return await service.list_rows(session, current.org_id, batch_id)


def _sse(state: Any, stats: Any) -> str:
    return f"data: {json.dumps({'state': state, 'stats': stats})}\n\n"


async def sse_events(
    redis: Redis, batch_id: UUID, initial_state: str, initial_stats: dict[str, Any],
    *, block_ms: int = 2000, max_idle_ticks: int | None = None,
) -> AsyncIterator[str]:
    """Yield SSE frames for a batch: the current state immediately, then every new
    `import.batch_state` for it (via the Redis event stream) until it reaches a terminal state. The
    latency floor is `block_ms` (the XREAD block), so a state change is delivered well under 2s."""
    yield _sse(initial_state, initial_stats)
    if is_terminal(BatchState(initial_state)):
        return
    last_id = "$"
    idle = 0
    while max_idle_ticks is None or idle < max_idle_ticks:
        resp: Any = await redis.xread({_STREAM: last_id}, block=block_ms, count=50)
        if not resp:
            idle += 1
            continue
        idle = 0
        for _stream, entries in resp:
            for entry_id, fields in entries:
                last_id = entry_id if isinstance(entry_id, str) else entry_id.decode()
                raw = fields["data"] if "data" in fields else fields[b"data"]
                envelope = json.loads(raw)
                data = envelope.get("data") or {}
                if str(data.get("batch_id")) == str(batch_id):
                    yield _sse(data.get("state"), data.get("stats"))
                    if is_terminal(BatchState(str(data.get("state")))):
                        return


@router.get("/{batch_id}/stream", summary="SSE relay of a batch's state changes")
async def stream_import(
    batch_id: UUID,
    current: CurrentAuth = Depends(requires(CATALOG_READ)),
    session: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no organization context")
    batch = await service.get_batch(session, current.org_id, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown import batch")
    redis: Redis = Redis.from_url(get_settings().redis_url)
    return StreamingResponse(
        sse_events(redis, batch_id, str(batch["state"]), dict(batch["stats"] or {})),
        media_type="text/event-stream",
    )
