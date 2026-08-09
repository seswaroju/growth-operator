"""Photo/vision extraction (MVP-077, gated-simulated) against real Postgres.

Proves the default (provider disabled) path produces a deterministic simulated row per image (a
low-confidence, `simulated_vision`-flagged placeholder for review), and that enabling the LLM
provider without a wired vision worker fails closed (`provider_unavailable`). Skips if DB is down.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.common.errors import GrowthOperatorError
from core.ingestion import extract_photo, service
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
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Photos')", oid)
    finally:
        await conn.close()
    yield oid
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM import_rows WHERE org_id=$1", oid)
        await conn.execute("DELETE FROM import_batches WHERE org_id=$1", oid)
        await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _photo_batch(org: uuid.UUID, images: int) -> uuid.UUID:
    async with org_scoped_session(org) as s:
        res = await service.create_batch(
            s, org, source_kind="photo", filename="p.jpg", data=b"fake-image-bytes",
            image_count=images)
        await s.commit()
    return uuid.UUID(str(res["batch_id"]))


async def test_simulated_photo_extraction_produces_placeholder_rows(org: uuid.UUID) -> None:
    batch_id = await _photo_batch(org, 3)
    async with org_scoped_session(org) as s:
        n = await extract_photo.extract_photos(s, org, batch_id)
        await s.commit()
    assert n == 3
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            "SELECT normalized, confidence, flags FROM import_rows WHERE batch_id=$1 ORDER BY seq",
            batch_id)
    finally:
        await conn.close()
    assert len(rows) == 3
    assert json.loads(rows[0]["normalized"])["title"] == "Photo item 1"
    assert json.loads(rows[0]["confidence"]) == 0.5
    assert "simulated_vision" in json.loads(rows[0]["flags"])
    async with org_scoped_session(org) as s:
        b = await service.get_batch(s, org, batch_id)
    assert b is not None and b["state"] == "extracted"


async def test_photo_gate_fails_closed_when_provider_enabled(
    org: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_PROVIDER_ENABLED", "true")
    batch_id = await _photo_batch(org, 2)
    async with org_scoped_session(org) as s:
        with pytest.raises(GrowthOperatorError) as ei:
            await extract_photo.extract_photos(s, org, batch_id)
    assert ei.value.code == "provider_unavailable"  # real vision not wired → fail closed
