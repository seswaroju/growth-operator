"""CSV/XLSX extraction + column mapping (MVP-078) against real Postgres.

Proves a CSV upload becomes `import_rows` (columns mapped to catalog fields, price coerced to minor,
unmapped columns → attributes, a title-less row flagged), that XLSX parses via openpyxl, that a
saved per-signature mapping overrides the auto-map, and that the batch advances to `extracted`.
Skips when the DB is unreachable.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.ingestion import extract_csv, service
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
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Importer')", oid)
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


async def _rows(org: uuid.UUID, batch_id: uuid.UUID) -> list[dict]:
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            "SELECT seq, normalized, flags, state FROM import_rows "
            "WHERE batch_id=$1 ORDER BY seq", batch_id)
        import json
        return [{"seq": r["seq"], "normalized": json.loads(r["normalized"]),
                 "flags": json.loads(r["flags"]), "state": r["state"]} for r in rows]
    finally:
        await conn.close()


async def _extract(org: uuid.UUID, *, source_kind: str, data: bytes) -> uuid.UUID:
    async with org_scoped_session(org) as s:
        res = await service.create_batch(
            s, org, source_kind=source_kind, filename=f"f.{source_kind}", data=data)
        await s.commit()
    batch_id = uuid.UUID(str(res["batch_id"]))
    async with org_scoped_session(org) as s:
        await extract_csv.extract_batch(s, org, batch_id)
        await s.commit()
    return batch_id


async def test_csv_extraction_maps_fields_and_flags_missing_title(org: uuid.UUID) -> None:
    csv_bytes = b"Name,SKU,Price,Color\nWidget A,SKU1,1234.50,Blue\n,SKU2,500,Red\n"
    batch_id = await _extract(org, source_kind="csv", data=csv_bytes)
    rows = await _rows(org, batch_id)
    assert len(rows) == 2
    r0 = rows[0]["normalized"]
    assert r0["title"] == "Widget A" and r0["sku"] == "SKU1"
    assert r0["base_price_minor"] == 123450             # ₹1,234.50 → minor
    assert r0["attributes"]["Color"] == "Blue"           # unmapped column → attribute
    assert rows[0]["flags"] == []
    assert "missing_title" in rows[1]["flags"]           # the title-less row is flagged
    async with org_scoped_session(org) as s:
        b = await service.get_batch(s, org, batch_id)
    assert b is not None and b["state"] == "extracted" and b["row_count"] == 2


async def test_xlsx_extraction_via_openpyxl(org: uuid.UUID) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Name", "SKU", "Price"])
    ws.append(["Gadget B", "SKU9", 999])
    buf = io.BytesIO()
    wb.save(buf)
    batch_id = await _extract(org, source_kind="xlsx", data=buf.getvalue())
    rows = await _rows(org, batch_id)
    assert len(rows) == 1
    assert rows[0]["normalized"]["title"] == "Gadget B"
    assert rows[0]["normalized"]["base_price_minor"] == 99900


async def test_saved_mapping_overrides_the_auto_map(org: uuid.UUID) -> None:
    headers = ["Item", "Cost"]
    signature = extract_csv._signature(headers)
    # remember a custom map BEFORE extraction (auto-map wouldn't know "Cost")
    async with org_scoped_session(org) as s:
        await extract_csv.save_mapping(
            s, org, signature, {"Item": "title", "Cost": "base_price_minor"})
        await s.commit()
    batch_id = await _extract(org, source_kind="csv", data=b"Item,Cost\nThing,750\n")
    r0 = (await _rows(org, batch_id))[0]["normalized"]
    assert r0["title"] == "Thing" and r0["base_price_minor"] == 75000  # saved mapping applied


async def test_extraction_failure_marks_batch_failed(org: uuid.UUID) -> None:
    # A batch whose blob is missing → extraction fails and the batch becomes 'failed' (resumable).
    async with org_scoped_session(org) as s:
        res = await service.create_batch(
            s, org, source_kind="csv", filename="x.csv", data=b"A\n1\n")
        await s.commit()
    batch_id = uuid.UUID(str(res["batch_id"]))
    # break the storage ref so load() returns None
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "UPDATE import_batches SET storage_ref='missing' WHERE id=$1", batch_id)
    finally:
        await conn.close()
    async with org_scoped_session(org) as s:
        with pytest.raises(extract_csv.ExtractionFailed):
            await extract_csv.extract_batch(s, org, batch_id)
        await s.commit()
    async with org_scoped_session(org) as s:
        b = await service.get_batch(s, org, batch_id)
    assert b is not None and b["state"] == "failed"
