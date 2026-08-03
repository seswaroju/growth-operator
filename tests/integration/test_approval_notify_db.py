"""Approval notification + ladder against real Postgres (MVP-068).

notify stamps `notified_at` and sends (simulated); the `approval.requested` consumer notifies;
a button/text reply resolves; and the ladder fires **remind → escalate → expire** on schedule.
Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from core.approvals import notify, service
from core.approvals.notify import SimulatedNotifier
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
        return bool(await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='approvals' AND column_name='notified_at'"))
    finally:
        await conn.close()


class Scene:
    def __init__(self, org: uuid.UUID, user: uuid.UUID) -> None:
        self.org = org
        self.user = user


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/approvals notify columns (MVP-068) not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org, user = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'N')", org)
        await conn.execute(
            "INSERT INTO users (id, phone, auth_provider) VALUES ($1,$2,'otp')",
            user, f"+1888{user.int % 10_000_000:07d}")
    finally:
        await conn.close()
    yield Scene(org, user)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM approvals WHERE org_id=$1", org)
        await conn.execute("DELETE FROM event_outbox WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM users WHERE id=$1", user)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _create(scene: Scene, *, action: str = "messages.send",
                  payload: dict | None = None) -> uuid.UUID:
    async with org_scoped_session(scene.org) as s:
        aid = await service.create_approval(
            s, scene.org, action_type=action, tier=2, payload=payload or {"amount_minor": 1000})
        await s.commit()
    return aid


async def _col(approval_id: uuid.UUID, col: str) -> object:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(f"SELECT {col} FROM approvals WHERE id=$1", approval_id)
    finally:
        await conn.close()


async def test_notify_stamps_and_sends(scene: Scene) -> None:
    aid = await _create(scene)
    notifier = SimulatedNotifier()
    async with org_scoped_session(scene.org) as s:
        ref = await notify.notify_approval(s, scene.org, aid, notifier=notifier)
        await s.commit()
    assert ref and len(notifier.sent) == 1
    assert await _col(aid, "notified_at") is not None


async def test_requested_consumer_notifies(scene: Scene) -> None:
    aid = await _create(scene)
    envelope = {"subject": str(scene.org), "data": {"approval_id": str(aid)}}
    await notify.on_approval_requested(envelope)  # uses the default (simulated) notifier
    assert await _col(aid, "notified_at") is not None


async def test_button_reply_resolves(scene: Scene) -> None:
    aid = await _create(scene)
    async with org_scoped_session(scene.org) as s:
        result = await notify.handle_button_reply(
            s, scene.org, approver_user_id=scene.user, button_id=f"approve:{aid}")
        await s.commit()
    assert result is not None and result.status == "approved"
    assert await _col(aid, "status") == "approved"


async def test_text_reply_resolves_latest_pending(scene: Scene) -> None:
    await _create(scene)                 # older
    latest = await _create(scene)        # newest — the ✅ should resolve this one
    async with org_scoped_session(scene.org) as s:
        result = await notify.handle_text_reply(
            s, scene.org, approver_user_id=scene.user, text_reply="✅ yes")
        await s.commit()
    assert result is not None and result.approval_id == latest and result.status == "approved"


async def _insert_timed(scene: Scene, *, created_min_ago: int, expires_in_min: int) -> uuid.UUID:
    now = datetime.now(UTC)
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(
            "INSERT INTO approvals (org_id, action_type, tier, payload, created_at, expires_at) "
            "VALUES ($1,'x',2,'{}'::jsonb,$2,$3) RETURNING id",
            scene.org, now - timedelta(minutes=created_min_ago),
            now + timedelta(minutes=expires_in_min))
    finally:
        await conn.close()


async def test_ladder_remind_escalate_expire(scene: Scene) -> None:
    remind = await _insert_timed(scene, created_min_ago=31, expires_in_min=29)   # 51% → remind
    escalate = await _insert_timed(scene, created_min_ago=46, expires_in_min=14)  # 77% → escalate
    expire = await _insert_timed(scene, created_min_ago=61, expires_in_min=-1)    # past → expire
    notifier = SimulatedNotifier()
    async with org_scoped_session(scene.org) as s:
        await notify._ladder_for_org(s, scene.org, datetime.now(UTC), notifier)
        await s.commit()

    assert await _col(remind, "reminded_at") is not None
    assert await _col(remind, "escalated_at") is None and await _col(remind, "status") == "pending"
    assert await _col(escalate, "escalated_at") is not None
    assert await _col(expire, "status") == "expired"
    kinds = sorted(k for _o, _a, k, _m in notifier.sent)
    assert kinds == ["escalate", "remind"]  # the two live ones notified; the expired one did not
