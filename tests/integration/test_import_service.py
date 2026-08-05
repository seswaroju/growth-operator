"""Import batch service (MVP-076) — event emission + legal-only transitions on real Postgres."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.ingestion import service
from core.ingestion.state import BatchState, IllegalTransition
from core.tenancy.middleware import org_scoped_session


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.import_batches')"))
    finally:
        await conn.close()


@pytest.fixture()
async def org() -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres/import_batches not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org_id = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'IS')", org_id)
    finally:
        await conn.close()
    yield org_id
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM import_batches WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM event_outbox WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org_id)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _outbox_states(org_id: uuid.UUID) -> list[str]:
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            "SELECT payload FROM event_outbox WHERE org_id=$1 AND type='import.batch_state.v1' "
            "ORDER BY id", org_id)
        return [json.loads(r["payload"])["state"] for r in rows]
    finally:
        await conn.close()


async def test_create_inserts_batch_and_emits_state(org: uuid.UUID) -> None:
    async with org_scoped_session(org) as s:
        batch = await service.create_batch(
            s, org, source_kind="csv", filename="stock.csv", data=b"h\na\nb\n")
        await s.commit()
    assert batch["state"] == "created" and batch["stats"]["row_count"] == 2
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT state, source_kind, row_count FROM import_batches WHERE id=$1",
            batch["batch_id"])
    finally:
        await conn.close()
    assert row["state"] == "created" and row["source_kind"] == "csv" and row["row_count"] == 2
    assert await _outbox_states(org) == ["created"]


async def test_transition_legal_then_rejects_illegal(org: uuid.UUID) -> None:
    async with org_scoped_session(org) as s:
        batch = await service.create_batch(s, org, source_kind="csv", filename="f", data=b"h\na\n")
        await s.commit()
    bid = batch["batch_id"]
    async with org_scoped_session(org) as s:  # created -> extracting is legal
        assert await service.transition(s, org, bid, BatchState.extracting) == BatchState.extracting
        await s.commit()
    async with org_scoped_session(org) as s:  # extracting -> loaded is illegal
        with pytest.raises(IllegalTransition):
            await service.transition(s, org, bid, BatchState.loaded)
    # only the two legal moves emitted a state event; the illegal one did not (order-independent —
    # the outbox identity sequence caches per connection, so insertion order isn't query order).
    assert set(await _outbox_states(org)) == {"created", "extracting"}
