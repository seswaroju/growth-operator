"""Row review queue (MVP-079) — confirm/edit/reject extracted rows before load.

After extraction, `validate_batch` advances the batch `extracted → validating → review`, flagging
rows: a title-less row can't load (`missing_title`); a row whose sku already exists in the catalog,
or repeats within the batch, is a `duplicate_sku`. The owner then confirms / edits / rejects rows
(bulk-confirm when uniform), with an **auto-approve** path for a high-confidence batch (all rows
≥0.95 AND no flags AND a ≥5% sample confirmed first). Only confirmed rows load (MVP-080). Per-org.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.ingestion.service import transition
from core.ingestion.state import BatchState
from core.tenancy.repository import set_org_context

BLOCKING_FLAGS = frozenset({"missing_title"})  # a row with any of these can't be confirmed/loaded
AUTO_CONFIDENCE = 0.95
AUTO_SAMPLE = 0.05  # 5% must be human-confirmed before the rest may auto-approve


def _obj(v: Any, default: Any) -> Any:
    if v is None:
        return default
    return json.loads(v) if isinstance(v, str) else v


async def _rows(session: AsyncSession, batch_id: UUID) -> list[dict[str, Any]]:
    rows = (await session.execute(
        text("SELECT seq, normalized, confidence, flags, state FROM import_rows "
             "WHERE batch_id = :b ORDER BY seq"), {"b": str(batch_id)})).mappings().all()
    return [dict(r) for r in rows]


async def validate_batch(session: AsyncSession, org_id: UUID, batch_id: UUID) -> dict[str, int]:
    """Advance `extracted → validating → review`, flagging duplicate skus (catalog + in-batch)."""
    await set_org_context(session, org_id)
    await transition(session, org_id, batch_id, BatchState.validating)
    seen: dict[str, int] = {}
    for r in await _rows(session, batch_id):
        norm = _obj(r["normalized"], {})
        flags = [f for f in _obj(r["flags"], []) if f != "duplicate_sku"]
        sku = str(norm.get("sku") or "").strip()
        dup = False
        if sku:
            if sku in seen:
                dup = True
            else:
                seen[sku] = r["seq"]
                exists = (await session.execute(
                    text("SELECT 1 FROM catalog_items WHERE sku = :s "
                         "AND status = 'active' LIMIT 1"), {"s": sku})).first()
                dup = exists is not None
        if dup:
            flags.append("duplicate_sku")
        await session.execute(
            text("UPDATE import_rows SET flags = CAST(:f AS jsonb) "
                 "WHERE batch_id = :b AND seq = :s"),
            {"f": json.dumps(flags), "b": str(batch_id), "s": r["seq"]})
    await transition(session, org_id, batch_id, BatchState.review)
    return await review_summary(session, org_id, batch_id)


async def review_summary(session: AsyncSession, org_id: UUID, batch_id: UUID) -> dict[str, int]:
    rows = await _rows(session, batch_id)
    confirmed = sum(1 for r in rows if r["state"] == "confirmed")
    rejected = sum(1 for r in rows if r["state"] == "rejected")
    flagged = sum(1 for r in rows if _obj(r["flags"], []))
    return {"total": len(rows), "confirmed": confirmed, "rejected": rejected, "flagged": flagged}


async def _one(session: AsyncSession, batch_id: UUID, seq: int) -> dict[str, Any] | None:
    row = (await session.execute(
        text("SELECT flags FROM import_rows WHERE batch_id = :b AND seq = :s"),
        {"b": str(batch_id), "s": seq})).mappings().first()
    return dict(row) if row else None


async def confirm_row(session: AsyncSession, org_id: UUID, batch_id: UUID, seq: int) -> None:
    await set_org_context(session, org_id)
    row = await _one(session, batch_id, seq)
    if row is None:
        raise KeyError("row not found")
    if set(_obj(row["flags"], [])) & BLOCKING_FLAGS:
        raise ValueError("row has blocking flags; edit it first")
    await session.execute(
        text("UPDATE import_rows SET state = 'confirmed' WHERE batch_id = :b AND seq = :s"),
        {"b": str(batch_id), "s": seq})


async def edit_row(
    session: AsyncSession, org_id: UUID, batch_id: UUID, seq: int, normalized: dict[str, Any]
) -> None:
    """Correct a row's normalized data → re-flag (title requirement) → confirm if clean."""
    await set_org_context(session, org_id)
    if await _one(session, batch_id, seq) is None:
        raise KeyError("row not found")
    flags: list[str] = [] if str(normalized.get("title") or "").strip() else ["missing_title"]
    new_state = "confirmed" if not flags else "extracted"
    await session.execute(
        text("UPDATE import_rows SET normalized = CAST(:n AS jsonb), flags = CAST(:f AS jsonb), "
             "state = :st WHERE batch_id = :b AND seq = :s"),
        {"n": json.dumps(normalized), "f": json.dumps(flags), "st": new_state,
         "b": str(batch_id), "s": seq})


async def reject_row(session: AsyncSession, org_id: UUID, batch_id: UUID, seq: int) -> None:
    await set_org_context(session, org_id)
    if await _one(session, batch_id, seq) is None:
        raise KeyError("row not found")
    await session.execute(
        text("UPDATE import_rows SET state = 'rejected' WHERE batch_id = :b AND seq = :s"),
        {"b": str(batch_id), "s": seq})


def _auto_ok(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    confirmed = sum(1 for r in rows if r["state"] == "confirmed")
    for r in rows:
        if float(_obj(r["confidence"], 0)) < AUTO_CONFIDENCE or _obj(r["flags"], []):
            return False
    return confirmed >= max(1, int(len(rows) * AUTO_SAMPLE))


async def confirm_all(
    session: AsyncSession, org_id: UUID, batch_id: UUID, *, auto: bool = False
) -> dict[str, int]:
    """Confirm every non-rejected, non-blocking row. `auto` requires the high-confidence gate."""
    await set_org_context(session, org_id)
    rows = await _rows(session, batch_id)
    if auto and not _auto_ok(rows):
        raise ValueError("batch does not meet auto-approve criteria")
    for r in rows:
        if r["state"] == "rejected" or set(_obj(r["flags"], [])) & BLOCKING_FLAGS:
            continue
        await session.execute(
            text("UPDATE import_rows SET state = 'confirmed' WHERE batch_id = :b AND seq = :s"),
            {"b": str(batch_id), "s": r["seq"]})
    return await review_summary(session, org_id, batch_id)
