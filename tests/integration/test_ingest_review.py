"""Row review queue (MVP-079) against real Postgres.

Proves `validate` flags a title-less row (blocking) and a duplicate-sku row (non-blocking) and moves
the batch to `review`; a blocking row can't be confirmed until edited; reject/bulk-confirm work; and
the auto-approve gate needs a high-confidence batch with a sample confirmed first. Skips if DB down.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.ingestion import extract_csv, review, service
from core.tenancy.middleware import org_scoped_session


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.import_rows')"))
    finally:
        await conn.close()


@pytest.fixture()
async def org() -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres/import_rows not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    oid = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Reviewer')", oid)
    finally:
        await conn.close()
    yield oid
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM import_rows WHERE org_id=$1", oid)
        await conn.execute("DELETE FROM import_batches WHERE org_id=$1", oid)
        await conn.execute("DELETE FROM tenant_settings WHERE org_id=$1", oid)
        await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _batch(org: uuid.UUID, csv: bytes) -> uuid.UUID:
    async with org_scoped_session(org) as s:
        res = await service.create_batch(s, org, source_kind="csv", filename="f.csv", data=csv)
        await s.commit()
    batch_id = uuid.UUID(str(res["batch_id"]))
    async with org_scoped_session(org) as s:
        await extract_csv.extract_batch(s, org, batch_id)
        await s.commit()
    return batch_id


async def _row_state(org: uuid.UUID, batch_id: uuid.UUID, seq: int) -> tuple[str, list[str]]:
    conn = await asyncpg.connect(_dsn())
    try:
        r = await conn.fetchrow(
            "SELECT state, flags FROM import_rows WHERE batch_id=$1 AND seq=$2", batch_id, seq)
        return r["state"], json.loads(r["flags"])
    finally:
        await conn.close()


async def test_validate_flags_and_review_lifecycle(org: uuid.UUID) -> None:
    # row0 clean · row1 title-less (blocking) · row2 repeats sku SKU1 (duplicate, non-blocking)
    csv = b"Name,SKU,Price\nWidget A,SKU1,1000\n,SKU2,500\nWidget C,SKU1,300\n"
    batch_id = await _batch(org, csv)

    async with org_scoped_session(org) as s:
        summary = await review.validate_batch(s, org, batch_id)
        await s.commit()
    assert summary["total"] == 3 and summary["flagged"] >= 2
    async with org_scoped_session(org) as s:
        b = await service.get_batch(s, org, batch_id)
    assert b is not None and b["state"] == "review"
    assert "missing_title" in (await _row_state(org, batch_id, 1))[1]
    assert "duplicate_sku" in (await _row_state(org, batch_id, 2))[1]

    async with org_scoped_session(org) as s:
        await review.confirm_row(s, org, batch_id, 0)          # clean row confirms
        with pytest.raises(ValueError):
            await review.confirm_row(s, org, batch_id, 1)      # blocking row can't confirm as-is
        await review.edit_row(s, org, batch_id, 1,             # fix the title → confirms
                              {"title": "Widget B", "sku": "SKU2", "base_price_minor": 50000,
                               "attributes": {}})
        await review.reject_row(s, org, batch_id, 2)           # drop the duplicate
        await s.commit()
    assert (await _row_state(org, batch_id, 0))[0] == "confirmed"
    assert (await _row_state(org, batch_id, 1))[0] == "confirmed"
    assert (await _row_state(org, batch_id, 2))[0] == "rejected"


async def test_bulk_confirm_skips_rejected_and_blocking(org: uuid.UUID) -> None:
    csv = b"Name,SKU,Price\nA,S1,100\n,S2,200\nC,S3,300\n"  # row1 title-less (blocking)
    batch_id = await _batch(org, csv)
    async with org_scoped_session(org) as s:
        await review.validate_batch(s, org, batch_id)
        await review.reject_row(s, org, batch_id, 2)
        summary = await review.confirm_all(s, org, batch_id)
        await s.commit()
    # row0 confirmed, row1 blocking (skipped, still flagged), row2 rejected
    assert summary["confirmed"] == 1 and summary["rejected"] == 1
    assert (await _row_state(org, batch_id, 1))[0] != "confirmed"


async def test_auto_approve_needs_high_confidence_and_a_sample(org: uuid.UUID) -> None:
    csv = b"Name,SKU,Price\nA,S1,100\nB,S2,200\n"  # both clean, confidence 1.0
    batch_id = await _batch(org, csv)
    async with org_scoped_session(org) as s:
        await review.validate_batch(s, org, batch_id)
        # no sample confirmed yet → auto-approve refused
        with pytest.raises(ValueError):
            await review.confirm_all(s, org, batch_id, auto=True)
        await review.confirm_row(s, org, batch_id, 0)  # confirm a sample (≥5%)
        summary = await review.confirm_all(s, org, batch_id, auto=True)
        await s.commit()
    assert summary["confirmed"] == 2
