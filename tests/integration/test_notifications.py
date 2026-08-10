"""Notification bell feed (MVP-075) against real Postgres.

The feed aggregates three existing signals (a pending approval, a ticket update, an automation
alert) into one list; the unread count is items newer than the user's `seen_at`, and opening the
bell (mark_seen) clears it — a later signal then shows as unread again. Skips without a DB.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.notifications import service
from core.tenancy.middleware import org_scoped_session


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.notification_reads')"))
    finally:
        await conn.close()


async def _pending_approval(conn: asyncpg.Connection, org: uuid.UUID) -> None:
    await conn.execute(
        "INSERT INTO approvals (org_id, action_type, tier, payload, status, expires_at) "
        "VALUES ($1,'messages.send',2,$2::jsonb,'pending', now() + interval '1 day')",
        org, json.dumps({"preview": "hi"}))


@pytest.fixture()
async def scene() -> AsyncIterator[dict[str, uuid.UUID]]:
    if not await _db_ready():
        pytest.skip("Postgres/notification_reads not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org, user = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Notif')", org)
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           user, f"n{user.hex[:8]}@x.com")
        await _pending_approval(conn, org)  # signal 1: approval
        await conn.execute(  # signal 2: a resolved ticket
            "INSERT INTO support_tickets (org_id, subject, description, status) "
            "VALUES ($1,'WhatsApp down','pls help','resolved')", org)
        d = await conn.fetchval(  # signal 3: a failed automation run
            "INSERT INTO workflow_definitions (org_id, workflow_key, version, dsl, trigger_spec) "
            "VALUES ($1,'reengage',1,'{}'::jsonb,'{}'::jsonb) RETURNING id", org)
        await conn.execute(
            "INSERT INTO workflow_runs (org_id, definition_id, definition_version, status) "
            "VALUES ($1,$2,1,'failed')", org, d)
    finally:
        await conn.close()
    yield {"org": org, "user": user}
    conn = await asyncpg.connect(_dsn())
    try:
        for t in ("notification_reads", "approvals", "support_tickets", "workflow_runs",
                  "workflow_definitions", "event_outbox"):
            await conn.execute(f"DELETE FROM {t} WHERE org_id=$1", org)
        await conn.execute("DELETE FROM users WHERE id=$1", user)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_feed_aggregates_the_three_signals(scene: dict[str, uuid.UUID]) -> None:
    org, user = scene["org"], scene["user"]
    async with org_scoped_session(org) as s:
        feed = await service.get_feed(s, org, user)
    assert {i["kind"] for i in feed["items"]} == {"approval", "ticket", "automation"}
    assert feed["unread_count"] == 3  # nothing seen yet
    # Newest-first ordering.
    ats = [i["at"] for i in feed["items"]]
    assert ats == sorted(ats, reverse=True)


async def test_mark_seen_clears_then_a_new_signal_is_unread(scene: dict[str, uuid.UUID]) -> None:
    org, user = scene["org"], scene["user"]
    async with org_scoped_session(org) as s:
        await service.mark_seen(s, org, user)
        await s.commit()
    async with org_scoped_session(org) as s:
        assert (await service.get_feed(s, org, user))["unread_count"] == 0
    # A new approval arrives after the bell was opened.
    conn = await asyncpg.connect(_dsn())
    try:
        await _pending_approval(conn, org)
    finally:
        await conn.close()
    async with org_scoped_session(org) as s:
        feed = await service.get_feed(s, org, user)
    assert feed["unread_count"] == 1  # only the new one is unread
