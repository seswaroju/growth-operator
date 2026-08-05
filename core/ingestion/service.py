"""Import batch service (MVP-076).

Creates a batch from an upload (enforcing the per-batch caps), lists batches/rows, and drives the
batch **state machine** — every transition persists and emits `import.batch_state.v1` (which the SSE
relay streams to the onboarding wizard). Extraction/normalize/validate/load fill `import_rows` and
advance the state in later tickets (077–080); this ticket lays the foundation. Per-org (RLS).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events.outbox import emit
from core.ingestion.state import BatchState, advance
from core.ingestion.storage import ImportBlobStore, default_store

# Per-batch caps (docs/21-platform/data-ingestion.md). Larger uploads must be chunked.
MAX_BYTES = 500 * 1024 * 1024
MAX_IMAGES = 200
MAX_ROWS = 5000
_STATE_EVENT = "import.batch_state.v1"


class CapExceeded(Exception):
    """A batch exceeds a per-batch cap — carries a client-facing chunking hint."""

    def __init__(self, detail: str, hint: str) -> None:
        super().__init__(detail)
        self.detail = detail
        self.hint = hint


def _csv_row_count(data: bytes) -> int:
    """Data rows in a CSV upload (excludes the header) — a cheap line count for the cap check."""
    lines = [ln for ln in data.decode("utf-8", errors="replace").splitlines() if ln.strip()]
    return max(0, len(lines) - 1)


def _check_caps(source_kind: str, data: bytes, image_count: int) -> int | None:
    """Enforce the byte / image / row caps. Returns the counted row_count (csv) or None. Raises
    `CapExceeded` with a chunking hint when a cap is exceeded."""
    if len(data) > MAX_BYTES:
        raise CapExceeded(
            f"upload is {len(data)} bytes, over the {MAX_BYTES}-byte cap",
            f"split the upload into files under {MAX_BYTES // (1024 * 1024)}MB")
    if source_kind == "photo" and image_count > MAX_IMAGES:
        raise CapExceeded(
            f"{image_count} images, over the {MAX_IMAGES}-image cap",
            f"upload at most {MAX_IMAGES} images per batch")
    if source_kind == "csv":
        rows = _csv_row_count(data)
        if rows > MAX_ROWS:
            raise CapExceeded(
                f"{rows} rows, over the {MAX_ROWS}-row cap",
                f"split the CSV into chunks of at most {MAX_ROWS} rows")
        return rows
    return None  # xlsx row-count is enforced at extraction (MVP-078)


async def create_batch(
    session: AsyncSession, org_id: UUID, *, source_kind: str, filename: str | None, data: bytes,
    image_count: int = 0, created_by: UUID | None = None, store: ImportBlobStore | None = None,
) -> dict[str, Any]:
    """Enforce caps, persist the blob, insert the batch (`created`), and emit its first state."""
    row_count = _check_caps(source_kind, data, image_count)
    store = store or default_store()
    storage_ref = await store.store(org_id, data)
    stats = {"row_count": row_count, "image_count": image_count, "byte_size": len(data)}
    batch_id = (
        await session.execute(
            text(
                "INSERT INTO import_batches (org_id, source_kind, state, filename, byte_size, "
                " image_count, row_count, storage_ref, stats, created_by) "
                "VALUES (:o, :sk, 'created', :fn, :bs, :ic, :rc, :ref, CAST(:st AS jsonb), :cb) "
                "RETURNING id"
            ),
            {"o": str(org_id), "sk": source_kind, "fn": filename, "bs": len(data),
             "ic": image_count, "rc": row_count, "ref": storage_ref, "st": json.dumps(stats),
             "cb": str(created_by) if created_by else None},
        )
    ).scalar_one()
    await emit(session, org_id=org_id, event_type=_STATE_EVENT, source="ingestion",
               payload={"batch_id": str(batch_id), "state": "created", "stats": stats})
    return {"batch_id": batch_id, "state": "created", "source_kind": source_kind, "stats": stats}


async def transition(
    session: AsyncSession, org_id: UUID, batch_id: UUID, target: BatchState
) -> BatchState:
    """Legal-only state transition: load the current state, `advance` (raises on an illegal move),
    persist, and emit `import.batch_state`."""
    row = (
        await session.execute(
            text("SELECT state, stats FROM import_batches WHERE id = :id"), {"id": str(batch_id)}
        )
    ).mappings().first()
    if row is None:
        raise KeyError(f"unknown import batch {batch_id}")
    new_state = advance(BatchState(row["state"]), target)  # raises IllegalTransition
    await session.execute(
        text("UPDATE import_batches SET state = :s, updated_at = now() WHERE id = :id"),
        {"s": str(new_state), "id": str(batch_id)},
    )
    await emit(session, org_id=org_id, event_type=_STATE_EVENT, source="ingestion",
               payload={"batch_id": str(batch_id), "state": str(new_state),
                        "stats": dict(row["stats"] or {})})
    return new_state


async def list_batches(session: AsyncSession, org_id: UUID) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            "SELECT id, source_kind, state, filename, byte_size, image_count, row_count, stats, "
            " created_at FROM import_batches ORDER BY created_at DESC"
        )
    )
    return [dict(r) for r in rows.mappings()]


async def get_batch(
    session: AsyncSession, org_id: UUID, batch_id: UUID
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT id, source_kind, state, filename, byte_size, image_count, row_count, "
                " stats, error, created_at FROM import_batches WHERE id = :id"
            ),
            {"id": str(batch_id)},
        )
    ).mappings().first()
    return dict(row) if row else None


async def list_rows(
    session: AsyncSession, org_id: UUID, batch_id: UUID
) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            "SELECT id, seq, raw, normalized, confidence, flags, state "
            "FROM import_rows WHERE batch_id = :b ORDER BY seq"
        ),
        {"b": str(batch_id)},
    )
    return [dict(r) for r in rows.mappings()]
