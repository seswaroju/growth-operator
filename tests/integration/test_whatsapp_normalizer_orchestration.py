"""The WhatsApp normalizer is actually *run*, and is safe to run twice at once (PILOT-1D-L, #47).

`normalize_pending` existed and worked from the day it was written, but nothing in production ever
called it — only tests did, and each test supplied the missing caller itself, which is precisely why
the gap survived a full suite. Physical Meta ingress exposed it: a real message reached
`webhook_events` and stopped there.

So these tests deliberately never call `normalize_pending` as the business action. They drive the
worker-owned loop, the same one `core.worker` runs, because "the function works" was already true
and was not the thing that was broken.

Against real Postgres under `app_rw`. Skips when the database is unreachable.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from core.channels.whatsapp import normalizer
from core.common import db as dbmod
from core.common.config import get_settings


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


def _webhook(pnid: str, wamid: str, phone: str, body: str) -> str:
    return json.dumps(
        {"entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": pnid},
            "messages": [{"id": wamid, "from": phone, "type": "text", "text": {"body": body}}],
        }}]}]}
    )


def _template_status(wamid: str) -> str:
    """A template-status update — owned by templates.py, and never this normalizer's to touch."""
    return json.dumps(
        {"entry": [{"changes": [{
            "field": "message_template_status_update",
            "value": {"message_template_id": wamid, "event": "APPROVED"},
        }]}]}
    )


class Scene:
    def __init__(self, org: uuid.UUID, pnid: str) -> None:
        self.org = org
        self.pnid = pnid
        self.wamids: list[str] = []

    async def drop_webhook(self, body: str = "Hello Vaylorn", *, raw: str | None = None) -> str:
        """Land a raw webhook exactly as the HTTP ingress does — durable row, nothing normalized."""
        wamid = f"wamid.{uuid.uuid4().hex}"
        self.wamids.append(wamid)
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute(
                "INSERT INTO webhook_events (provider, external_id, payload) "
                "VALUES ('whatsapp', $1, $2::jsonb)",
                wamid, raw if raw is not None else _webhook(self.pnid, wamid, "919000000001", body),
            )
        finally:
            await conn.close()
        return wamid

    async def counts(self, wamid: str) -> dict[str, Any]:
        conn = await asyncpg.connect(_dsn())
        try:
            return {
                "messages": await conn.fetchval(
                    "SELECT count(*) FROM messages WHERE provider_message_id=$1", wamid),
                "events": await conn.fetchval(
                    "SELECT count(*) FROM event_outbox WHERE org_id=$1 AND type='msg.received.v1'",
                    self.org),
                "processed_at": await conn.fetchval(
                    "SELECT processed_at FROM webhook_events WHERE external_id=$1", wamid),
            }
        finally:
            await conn.close()


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
        ready = bool(await conn.fetchval("SELECT to_regclass('public.channels')"))
        await conn.close()
    except Exception:
        ready = False
    if not ready:
        pytest.skip("Postgres/messaging+channel_resolve not ready")

    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    pnid = f"pnid-{org.hex[:8]}"
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'W')", org)
        await conn.execute(
            "INSERT INTO channels (org_id, type, external_id, credentials_ref) "
            "VALUES ($1,'whatsapp',$2,'ref')", org, pnid)
    finally:
        await conn.close()

    s = Scene(org, pnid)
    yield s

    conn = await asyncpg.connect(_dsn())
    try:
        # Only this scene's own rows. The older normalizer suite clears every whatsapp webhook by
        # provider, which would delete a concurrently running test's fixture — and, on a developer
        # machine, a real pilot webhook that had not been normalized yet.
        if s.wamids:
            await conn.execute("DELETE FROM webhook_events WHERE external_id = ANY($1)", s.wamids)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _run_until_processed(
    scene: Scene, wamid: str, *, timeout: float = 5.0
) -> asyncio.Event:
    """Start the real loop, wait for it to do the work by itself, then stop it."""
    stop = asyncio.Event()
    task = asyncio.create_task(normalizer.run_normalizer(stop, poll_interval_s=0.05))
    try:
        async with asyncio.timeout(timeout):
            while (await scene.counts(wamid))["processed_at"] is None:
                await asyncio.sleep(0.05)
    finally:
        stop.set()
        await task
    return stop


# ---- (1) production orchestration --------------------------------------------------------------


async def test_the_worker_loop_normalizes_a_webhook_without_being_asked(scene: Scene) -> None:
    """The gap #47 describes. A raw webhook is normalized by the running loop alone — this test
    never calls `normalize_pending`, because a caller-supplied test is exactly what hid the bug."""
    wamid = await scene.drop_webhook("Hello Vaylorn 3")

    await _run_until_processed(scene, wamid)

    after = await scene.counts(wamid)
    assert after["messages"] == 1
    assert after["events"] == 1, "msg.received.v1 must reach the outbox for the planner to see it"
    assert after["processed_at"] is not None


async def test_the_worker_wires_the_normalizer_in(scene: Scene) -> None:
    """Orchestration is the deliverable, so the wiring itself is asserted rather than assumed —
    a loop that exists but is never started is the same outage in a different place."""
    import inspect

    from core import worker

    source = inspect.getsource(worker.run_worker)
    assert "run_normalizer" in source
    assert worker.run_normalizer is normalizer.run_normalizer


# ---- (2) multi-worker concurrency --------------------------------------------------------------


async def test_two_normalizers_racing_one_webhook_produce_one_message(scene: Scene) -> None:
    """Two workers is the normal production shape, and both will see the same pending row. The
    row lock is taken in the transaction that does the work, so exactly one of them commits it."""
    wamid = await scene.drop_webhook("Racing message")

    # Same batch, at the same time, from independent sessions — the actual race.
    results = await asyncio.gather(
        normalizer.normalize_pending(10), normalizer.normalize_pending(10)
    )

    after = await scene.counts(wamid)
    assert after["messages"] == 1, "a customer message must never be ingested twice"
    assert after["events"] == 1, "the planner must not be handed the same message twice"
    assert after["processed_at"] is not None

    # The counts above are NOT proof of the lock on their own — `ON CONFLICT
    # (provider_message_id) DO NOTHING` would absorb a duplicate even with no locking at all, and
    # this assertion passes against the unlocked code. What the lock adds is that the duplicate
    # work never happens: exactly one worker reports the webhook as handled, and the other skips
    # it rather than re-running contact upsert, conversation open, media fetch and STOP handling.
    assert sum(results) == 1, f"exactly one worker should handle it, got {results}"


async def test_a_racing_worker_skips_rather_than_waits(scene: Scene) -> None:
    """`SKIP LOCKED`, not a plain `FOR UPDATE`: a worker that finds the row taken moves on instead
    of queueing behind it to redo work that is already committing."""
    wamid = await scene.drop_webhook("Locked message")
    factory = dbmod.get_sessionmaker()

    async with factory() as holder:
        held = await normalizer._claim(holder, (await _event_id(wamid)))
        assert held is not None, "the first claim must succeed"

        # A second worker, while the first still holds the lock.
        async with factory() as contender:
            assert await normalizer._claim(contender, (await _event_id(wamid))) is None
            await contender.rollback()
        await holder.rollback()

    # Released by the rollback, so the webhook is still available.
    async with factory() as later:
        assert await normalizer._claim(later, (await _event_id(wamid))) is not None
        await later.rollback()


async def _event_id(wamid: str) -> uuid.UUID:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval("SELECT id FROM webhook_events WHERE external_id=$1", wamid)
    finally:
        await conn.close()


# ---- (3) failure / retry -----------------------------------------------------------------------


async def test_a_failure_rolls_back_and_leaves_the_webhook_retryable(
    scene: Scene, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A webhook that fails mid-normalization must stay pending. Marking it processed to reserve it
    would mean a crash silently discarded a real customer message."""
    wamid = await scene.drop_webhook("Fails once")

    calls = {"n": 0}
    real = normalizer._normalize_one

    async def flaky(session: Any, event_id: Any, payload: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated failure after partial work")
        return await real(session, event_id, payload)

    monkeypatch.setattr(normalizer, "_normalize_one", flaky)

    assert await normalizer.normalize_pending(10) == 0
    failed = await scene.counts(wamid)
    assert failed["processed_at"] is None, "a failed webhook must remain pending"
    assert failed["messages"] == 0, "the failed transaction must not leave a message behind"

    # The lock was released by the rollback, so the retry succeeds — proven by running the loop.
    await _run_until_processed(scene, wamid)
    after = await scene.counts(wamid)
    assert after["messages"] == 1 and after["events"] == 1
    assert calls["n"] == 2


# ---- (5) template-status exclusion -------------------------------------------------------------


async def test_template_status_updates_are_left_for_their_owner(scene: Scene) -> None:
    """`templates.py` drains those. The normalizer must not claim, process or mark them — the
    predicate is shared between the scan and the claim so neither can start doing so by drift."""
    wamid = await scene.drop_webhook(raw=_template_status("tmpl-1"))

    stop = asyncio.Event()
    task = asyncio.create_task(normalizer.run_normalizer(stop, poll_interval_s=0.05))
    await asyncio.sleep(0.3)  # several full passes
    stop.set()
    await task

    after = await scene.counts(wamid)
    assert after["processed_at"] is None, "template status is not this normalizer's to process"
    assert after["messages"] == 0

    # And it is not even claimable here.
    factory = dbmod.get_sessionmaker()
    async with factory() as session:
        assert await normalizer._claim(session, await _event_id(wamid)) is None
        await session.rollback()


# ---- (6) graceful shutdown ---------------------------------------------------------------------


async def test_the_loop_stops_promptly_when_the_worker_stops(scene: Scene) -> None:
    """Shutdown must not wait out a poll interval, and must not leave the task hanging — the worker
    gathers this task with the publisher and the consumers."""
    stop = asyncio.Event()
    task = asyncio.create_task(normalizer.run_normalizer(stop, poll_interval_s=30.0))
    await asyncio.sleep(0.1)  # let it reach the idle wait

    stop.set()
    async with asyncio.timeout(2.0):  # far below the 30s poll interval
        await task

    assert task.done() and not task.cancelled()
    assert task.exception() is None


async def test_a_broken_batch_does_not_kill_the_loop(
    scene: Scene, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This task runs inside the worker's `gather`, so an escaping exception would take the outbox
    publisher and every consumer down with it. One bad scan must cost one poll interval."""
    calls = {"n": 0}

    async def boom(limit: int = 100) -> int:
        calls["n"] += 1
        raise RuntimeError("database blip")

    monkeypatch.setattr(normalizer, "normalize_pending", boom)

    stop = asyncio.Event()
    task = asyncio.create_task(normalizer.run_normalizer(stop, poll_interval_s=0.05))
    await asyncio.sleep(0.3)
    stop.set()
    await task

    assert task.exception() is None, "the loop must survive a failing batch"
    assert calls["n"] > 1, "it must keep trying rather than stop after the first failure"
