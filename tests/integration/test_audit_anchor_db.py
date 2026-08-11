"""Audit anchoring (MVP-071) — build a real anchor over an org's chain, then prove `verify` is clean
on an untouched chain and FLAGS a rewrite or a truncation (tamper-evidence). Skips without the DB.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.audit import anchor
from core.audit import write as audit_write
from core.audit.writer import AuditEntry
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.middleware import org_scoped_session


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.audit_log')"))
    finally:
        await conn.close()


@pytest.fixture()
async def org() -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres/audit_log not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    o = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'ANCH')", o)
    finally:
        await conn.close()
    # Three chained audit entries → a 3-deep chain for this org.
    async with org_scoped_session(o) as s:
        for i in range(3):
            await audit_write(s, AuditEntry(
                org_id=o, actor_type="user", action="settings.changed", resource=f"r{i}"))
    yield o
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", o)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM organizations WHERE id=$1", o)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _our_head(org: uuid.UUID) -> dict:
    record = await anchor.build_anchor()
    head = next(h for h in record["heads"] if h["org_id"] == str(org))
    return {"anchored_at": record["anchored_at"], "org_count": 1, "heads": [head]}


async def test_build_anchor_captures_the_head(org: uuid.UUID) -> None:
    head = (await _our_head(org))["heads"][0]
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT seq, entry_hash FROM audit_log WHERE org_id=$1 ORDER BY seq DESC LIMIT 1", org)
    finally:
        await conn.close()
    assert head["seq"] == row["seq"] == 3  # three entries → head at seq 3
    assert head["entry_hash"] == row["entry_hash"]


async def test_verify_clean_then_detects_a_rewrite(org: uuid.UUID) -> None:
    record = await _our_head(org)
    assert await anchor.verify_against_anchor(record) == []  # untouched → intact

    # An attacker with full DB access rewrites the head entry's hash (immutability trigger off).
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute(
            "UPDATE audit_log SET entry_hash='deadbeef' WHERE org_id=$1 AND seq=3", org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
    finally:
        await conn.close()
    problems = await anchor.verify_against_anchor(record)
    assert len(problems) == 1
    assert problems[0].org_id == str(org) and problems[0].seq == 3
    assert problems[0].current == "deadbeef" and problems[0].anchored != "deadbeef"


async def test_verify_detects_truncation(org: uuid.UUID) -> None:
    record = await _our_head(org)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1 AND seq=3", org)  # drop the head
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
    finally:
        await conn.close()
    problems = await anchor.verify_against_anchor(record)
    assert len(problems) == 1 and problems[0].current is None  # the anchored entry is gone
