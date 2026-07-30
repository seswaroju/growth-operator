"""Audit chain writer against a real Postgres (MVP-024).

Exercises the DB-backed behaviour: chain continuity, the 10-minute capability, the
append-only trigger, tamper detection (incl. the operator verify script), and per-org
concurrency with no gaps. Skips cleanly when the DB is unreachable.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import asyncpg
import pytest

from core.audit import AuditEntry, ChainRecord, verify_capability, verify_chain, write
from core.audit import write as audit_write
from core.common import db as dbmod
from core.common.config import get_settings

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


def _owner_dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_owner_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.audit_log') IS NOT NULL"))
    finally:
        await conn.close()


async def _fetch_chain(org: uuid.UUID) -> list[ChainRecord]:
    conn = await asyncpg.connect(_owner_dsn())
    try:
        rows = await conn.fetch(
            "SELECT seq, actor_type, actor_id, action, resource, payload, prev_hash, "
            "entry_hash, permission_manifest_hash FROM audit_log WHERE org_id=$1 ORDER BY seq",
            org,
        )
    finally:
        await conn.close()
    return [
        ChainRecord(
            seq=r["seq"], actor_type=r["actor_type"], actor_id=r["actor_id"],
            action=r["action"], resource=r["resource"],
            payload=(
                json.loads(r["payload"])
                if isinstance(r["payload"], str)
                else dict(r["payload"])
            ),
            prev_hash=r["prev_hash"], entry_hash=r["entry_hash"],
            permission_manifest_hash=r["permission_manifest_hash"],
        )
        for r in rows
    ]


async def _mk_org() -> uuid.UUID:
    org = uuid.uuid4()
    conn = await asyncpg.connect(_owner_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1, 'A')", org)
    finally:
        await conn.close()
    return org


async def _drop_org(org: uuid.UUID) -> None:
    conn = await asyncpg.connect(_owner_dsn())
    try:
        # audit_log has UPDATE/DELETE blocked, but the org FK cascade still needs DELETE on
        # audit_log rows — temporarily disable the immutability trigger for cleanup.
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id = $1", org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM organizations WHERE id = $1", org)
    finally:
        await conn.close()


@pytest.fixture()
async def org() -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres/migration 006 not ready")
    o = await _mk_org()
    yield o
    await _drop_org(o)
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_write_builds_chain_and_verifies(org: uuid.UUID) -> None:
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        for i in range(5):
            await write(
                s,
                AuditEntry(org_id=org, actor_type="user", action="message.send", resource=f"c{i}"),
            )
        await s.commit()

    records = await _fetch_chain(org)
    assert [r.seq for r in records] == [1, 2, 3, 4, 5]
    assert records[0].prev_hash == ""  # genesis
    assert verify_chain(records) is None


async def test_capability_expiry_and_match(org: uuid.UUID) -> None:
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        cap = await write(
            s, AuditEntry(org_id=org, actor_type="user", action="message.send", resource="conv1")
        )
        await s.commit()

    async with factory() as s:
        await s.execute(
            __import__("sqlalchemy").text("SELECT set_config('app.org_id', :o, true)"),
            {"o": str(org)},
        )
        assert await verify_capability(s, cap.id, action="message.send", resource="conv1") is True
        # wrong resource / action → not a valid capability
        assert await verify_capability(s, cap.id, action="message.send", resource="other") is False
        assert (
            await verify_capability(s, cap.id, action="approval.resolved", resource="conv1")
            is False
        )
        # expired (just past the 10-minute window)
        assert (
            await verify_capability(
                s, cap.id, action="message.send", resource="conv1",
                now=cap.expires_at + timedelta(seconds=1),
            )
            is False
        )


async def test_append_only_trigger_blocks_update_and_delete(org: uuid.UUID) -> None:
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        cap = await write(s, AuditEntry(org_id=org, actor_type="user", action="message.send"))
        await s.commit()

    # As the owner (who HAS update/delete privilege) the trigger still blocks mutation.
    conn = await asyncpg.connect(_owner_dsn())
    try:
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await conn.execute("UPDATE audit_log SET action='x' WHERE id=$1", cap.id)
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await conn.execute("DELETE FROM audit_log WHERE id=$1", cap.id)
    finally:
        await conn.close()


async def test_tamper_detected_by_verify_chain_and_script(org: uuid.UUID) -> None:
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        for i in range(5):
            await write(
                s,
                AuditEntry(
                    org_id=org, actor_type="system", action="pricing.computed", resource=str(i)
                ),
            )
        await s.commit()

    # Script reports a clean chain first (acceptance: audit-verify --org <fixture> clean).
    clean = subprocess.run(
        [sys.executable, "scripts/audit-verify.py", "--org", str(org)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert clean.returncode == 0, clean.stderr
    assert "intact" in clean.stdout

    # Tamper seq 3 (disable the trigger as owner to simulate a breach).
    conn = await asyncpg.connect(_owner_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute(
            "UPDATE audit_log SET action='tampered' WHERE org_id=$1 AND seq=3", org
        )
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
    finally:
        await conn.close()

    assert verify_chain(await _fetch_chain(org)) == 3
    broken = subprocess.run(
        [sys.executable, "scripts/audit-verify.py", "--org", str(org)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert broken.returncode == 1
    assert "seq 3" in broken.stderr


async def test_outcome_entry_appended(org: uuid.UUID) -> None:
    from core.audit import write_outcome

    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        cap = await write(
            s, AuditEntry(org_id=org, actor_type="user", action="message.send", resource="c")
        )
        await write_outcome(s, cap.id, "failed", detail="provider timeout")
        await s.commit()

    records = await _fetch_chain(org)
    assert [r.action for r in records] == ["message.send", "message.send:failed"]
    assert verify_chain(records) is None


async def test_concurrent_per_org_no_gaps_and_latency() -> None:
    if not await _db_ready():
        pytest.skip("no database")
    orgs = [await _mk_org() for _ in range(3)]
    per_org = 150  # ~450 writes; keeps the test quick while exercising cross-org concurrency
    factory = dbmod.get_sessionmaker()
    latencies: list[float] = []

    async def writer_task(o: uuid.UUID) -> None:
        async with factory() as s:
            for i in range(per_org):
                t0 = time.perf_counter()
                await audit_write(
                    s,
                    AuditEntry(
                        org_id=o, actor_type="system", action="pricing.computed", resource=str(i)
                    ),
                )
                latencies.append(time.perf_counter() - t0)
            await s.commit()

    try:
        await asyncio.gather(*(writer_task(o) for o in orgs))
        # No gaps: each org's seq is exactly 1..per_org.
        for o in orgs:
            seqs = [r.seq for r in await _fetch_chain(o)]
            assert seqs == list(range(1, per_org + 1)), f"gap in org {o}"
        latencies.sort()
        p95 = latencies[int(len(latencies) * 0.95)]
        print(f"\naudit write p95={p95 * 1000:.2f}ms over {len(latencies)} writes")
        # AC target is p95 < 3ms (measured ~1.1ms locally). Guard at 10ms for CI headroom.
        assert p95 < 0.01
    finally:
        for o in orgs:
            await _drop_org(o)
        await dbmod.get_engine().dispose()
        dbmod.get_engine.cache_clear()
        dbmod.get_sessionmaker.cache_clear()
