"""Load + revert (MVP-080) — confirmed rows become catalog items, reversibly.

`load_batch` advances `review → loading`, creates a `catalog_item` from each **confirmed** row
through `crud.create_item` (which validates attributes and enforces the pack's identity keys) and
stamps each with `import_batch_id`, then finishes `loading → loaded`. A duplicate identity or an
invalid row is skipped/failed per-row (never fails the whole batch). `revert_batch` (within 30 days)
soft-deletes the items this batch created that **haven't been edited since** and lists the mutated
ones for manual review. `reap_old_batches` frees the staging data for terminal batches past the
window. Per-org (RLS).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.catalog import crud
from core.catalog.validate import ValidationProblems
from core.ingestion.service import transition
from core.ingestion.state import BatchState
from core.ingestion.storage import default_store
from core.tenancy.repository import set_org_context

REVERT_WINDOW_DAYS = 30
_SYSTEM_ACTOR = UUID("00000000-0000-0000-0000-000000000000")
log = logging.getLogger(__name__)


async def _mark(session: AsyncSession, batch_id: UUID, seq: int, state: str) -> None:
    await session.execute(
        text("UPDATE import_rows SET state = :st WHERE batch_id = :b AND seq = :s"),
        {"st": state, "b": str(batch_id), "s": seq})


async def load_batch(session: AsyncSession, org_id: UUID, batch_id: UUID) -> dict[str, int]:
    """Load the batch's confirmed rows into the catalog. Returns loaded/skipped/failed counts."""
    await set_org_context(session, org_id)
    batch = (await session.execute(
        text("SELECT created_by FROM import_batches WHERE id = :id AND org_id = :o"),
        {"id": str(batch_id), "o": str(org_id)})).mappings().first()
    if batch is None:
        raise KeyError("unknown import batch")
    actor = UUID(str(batch["created_by"])) if batch["created_by"] else _SYSTEM_ACTOR
    await transition(session, org_id, batch_id, BatchState.loading)
    rows = (await session.execute(
        text("SELECT seq, normalized FROM import_rows "
             "WHERE batch_id = :b AND state = 'confirmed' ORDER BY seq"),
        {"b": str(batch_id)})).mappings().all()
    loaded = skipped = failed = 0
    for r in rows:
        norm = r["normalized"] if isinstance(r["normalized"], dict) else json.loads(r["normalized"])
        item = crud.ItemInput(
            title=str(norm.get("title") or ""), price_mode="static",
            attributes=norm.get("attributes") or {}, sku=(norm.get("sku") or None),
            description=(norm.get("description") or None),
            base_price_minor=norm.get("base_price_minor"))
        try:
            item_id, _ = await crud.create_item(
                session, org_id, item, actor_id=actor, import_batch_id=batch_id)
            await session.execute(
                text("UPDATE import_rows SET state = 'loaded', loaded_entity_id = :e "
                     "WHERE batch_id = :b AND seq = :s"),
                {"e": str(item_id), "b": str(batch_id), "s": r["seq"]})
            loaded += 1
        except crud.DuplicateIdentity:  # already in the catalog → don't duplicate it
            await _mark(session, batch_id, r["seq"], "skipped_duplicate")
            skipped += 1
        except ValidationProblems:  # invalid attributes → left for a re-review, not loaded
            await _mark(session, batch_id, r["seq"], "load_failed")
            failed += 1
    await transition(session, org_id, batch_id, BatchState.loaded)
    return {"loaded": loaded, "skipped": skipped, "failed": failed}


async def revert_batch(session: AsyncSession, org_id: UUID, batch_id: UUID) -> dict[str, Any]:
    """Undo a load within 30 days: archive this batch's UNMUTATED items; list the mutated ones."""
    await set_org_context(session, org_id)
    batch = (await session.execute(
        text("SELECT state, updated_at FROM import_batches WHERE id = :id AND org_id = :o"),
        {"id": str(batch_id), "o": str(org_id)})).mappings().first()
    if batch is None:
        raise KeyError("unknown import batch")
    if batch["state"] != "loaded":
        raise ValueError("only a loaded batch can be reverted")
    if datetime.now(UTC) - batch["updated_at"] > timedelta(days=REVERT_WINDOW_DAYS):
        raise ValueError("the 30-day revert window has closed")
    items = (await session.execute(
        text("SELECT id, (updated_at > created_at) AS mutated FROM catalog_items "
             "WHERE import_batch_id = :b AND status = 'active'"),
        {"b": str(batch_id)})).mappings().all()
    reverted = 0
    mutated: list[str] = []
    for it in items:
        if it["mutated"]:  # edited since the import → don't silently undo the owner's change
            mutated.append(str(it["id"]))
        else:
            await session.execute(
                text("UPDATE catalog_items SET status = 'archived', updated_at = now() "
                     "WHERE id = :id"), {"id": str(it["id"])})
            reverted += 1
    await transition(session, org_id, batch_id, BatchState.reverted)
    return {"reverted": reverted, "mutated_skipped": mutated}


# ---- Reaper: free staging data for terminal batches past the revert window ---------------------

async def reap_old_batches() -> int:
    """Delete import_rows + clear the blob ref for terminal batches older than the window. The
    loaded catalog items stay; only the staging data is freed. Returns the batch count reaped."""
    from core.common import db as dbmod

    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        org_ids = (await s.execute(text("SELECT id FROM organizations"))).scalars().all()
    reaped = 0
    for org_raw in org_ids:
        oid = UUID(str(org_raw))
        from core.tenancy.middleware import org_scoped_session

        async with org_scoped_session(oid) as s:
            batch_ids = (await s.execute(
                text("SELECT id FROM import_batches WHERE state IN "
                     "('loaded','reverted','cancelled','failed') AND storage_ref IS NOT NULL "
                     "AND updated_at < now() - make_interval(days => :d)"),
                {"d": REVERT_WINDOW_DAYS})).scalars().all()
            for bid in batch_ids:
                await s.execute(
                    text("DELETE FROM import_rows WHERE batch_id = :b"), {"b": str(bid)})
                await s.execute(
                    text("UPDATE import_batches SET storage_ref = NULL WHERE id = :b"),
                    {"b": str(bid)})
                default_store()  # in-process blob store: nothing to delete by ref in dev
                reaped += 1
    return reaped


async def _reap_job() -> None:
    reaped = await reap_old_batches()
    if reaped:
        log.info("import_batch_reaper freed staging data for %d batch(es)", reaped)


def register_jobs() -> None:
    """Register the daily import-batch reaper (03:45 UTC)."""
    from core.events import scheduler as sched

    sched.register("import_batch_reaper", "45 3 * * *", _reap_job)
