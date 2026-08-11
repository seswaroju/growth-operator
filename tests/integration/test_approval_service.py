"""Approval service lifecycle (MVP-067) against real Postgres.

Covers create + `approval.requested`, list, approve/reject + `approval.resolved`, **double-resolve
idempotency** (returns the first outcome, one resolved event), **edit re-evaluation** (an edit that
raises the tier is rejected with an explanation; a same-tier edit is approved), and the **410**
expired path. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.approvals import service
from core.approvals.service import ApprovalExpired
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.middleware import org_scoped_session

ACTION = "discount.apply"


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.approvals')"))
    finally:
        await conn.close()


class Scene:
    def __init__(self, org: uuid.UUID, user: uuid.UUID) -> None:
        self.org = org
        self.user = user


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/approvals object not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org, user = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'AP')", org)
        await conn.execute(
            "INSERT INTO users (id, phone, auth_provider) VALUES ($1,$2,'otp')",
            user, f"+1999{user.int % 10_000_000:07d}")
        pack = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"ap{org.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO pack_installations (org_id, pack_id, status) VALUES ($1,$2,'active')",
            org, pack)  # install so the pack's rules apply (per-pack scoping, #22)
        # discount.apply: tier 2 by default, tier 3 for big discounts (so an edit can escalate)
        await conn.execute(
            "INSERT INTO approval_policies (scope, pack_id, action_type, tier, description) "
            "VALUES ('pack',$1,$2,2,'base')", pack, ACTION)
        await conn.execute(
            "INSERT INTO approval_policies (scope, pack_id, action_type, tier, cel_expr, "
            " description) VALUES ('pack',$1,$2,3,'amount_minor > 50000','big')", pack, ACTION)
    finally:
        await conn.close()
    yield Scene(org, user)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM approvals WHERE org_id=$1", org)
        await conn.execute("DELETE FROM approval_policies WHERE pack_id=$1", pack)
        await conn.execute("DELETE FROM event_outbox WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM users WHERE id=$1", user)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _outbox_types(org: uuid.UUID) -> list[str]:
    conn = await asyncpg.connect(_dsn())
    try:
        return [r["type"] for r in await conn.fetch(
            "SELECT type FROM event_outbox WHERE org_id=$1 ORDER BY created_at", org)]
    finally:
        await conn.close()


async def _create(scene: Scene, *, tier: int = 2, payload: dict | None = None,
                  expires_in_s: int = 3600) -> uuid.UUID:
    async with org_scoped_session(scene.org) as s:
        aid = await service.create_approval(
            s, scene.org, action_type=ACTION, tier=tier,
            payload=payload or {"amount_minor": 1000}, expires_in_s=expires_in_s,
        )
        await s.commit()
    return aid


async def test_create_lists_and_announces(scene: Scene) -> None:
    aid = await _create(scene)
    async with org_scoped_session(scene.org) as s:
        pending = await service.list_approvals(s, scene.org, status="pending")
    assert [p["id"] for p in pending] == [aid]
    assert "approval.requested.v1" in await _outbox_types(scene.org)


async def test_approve_then_resolved_event(scene: Scene) -> None:
    aid = await _create(scene)
    async with org_scoped_session(scene.org) as s:
        result = await service.resolve(
            s, scene.org, aid, approver_user_id=scene.user, decision="approve")
        await s.commit()
    assert result.status == "approved" and result.idempotent_replay is False
    assert "approval.resolved.v1" in await _outbox_types(scene.org)


async def test_reject(scene: Scene) -> None:
    aid = await _create(scene)
    async with org_scoped_session(scene.org) as s:
        result = await service.resolve(
            s, scene.org, aid, approver_user_id=scene.user, decision="reject",
            reason_code="off_policy")
        await s.commit()
    assert result.status == "rejected"


async def test_double_resolve_is_idempotent(scene: Scene) -> None:
    aid = await _create(scene)
    async with org_scoped_session(scene.org) as s:
        first = await service.resolve(
            s, scene.org, aid, approver_user_id=scene.user, decision="approve")
        await s.commit()
    async with org_scoped_session(scene.org) as s:
        second = await service.resolve(
            s, scene.org, aid, approver_user_id=scene.user, decision="reject")  # different tap
        await s.commit()
    assert first.status == "approved" and first.idempotent_replay is False
    assert second.status == "approved" and second.idempotent_replay is True  # first outcome wins
    # exactly one resolved event despite two taps
    assert (await _outbox_types(scene.org)).count("approval.resolved.v1") == 1


async def test_edit_raising_tier_is_rejected_with_explanation(scene: Scene) -> None:
    aid = await _create(scene, tier=2, payload={"amount_minor": 1000})
    async with org_scoped_session(scene.org) as s:
        result = await service.resolve(
            s, scene.org, aid, approver_user_id=scene.user, decision="approve",
            edited_payload={"amount_minor": 60000},  # re-evaluates to tier 3
        )
        await s.commit()
    assert result.status == "rejected" and result.edited is True
    assert result.tier == 3 and result.note is not None and "tier" in result.note


async def test_edit_same_tier_is_approved(scene: Scene) -> None:
    aid = await _create(scene, tier=2, payload={"amount_minor": 1000})
    async with org_scoped_session(scene.org) as s:
        result = await service.resolve(
            s, scene.org, aid, approver_user_id=scene.user, decision="approve",
            edited_payload={"amount_minor": 2000},  # stays tier 2
        )
        await s.commit()
    assert result.status == "approved" and result.edited is True


async def test_resolve_expired_is_410(scene: Scene) -> None:
    aid = await _create(scene, expires_in_s=-10)  # already expired
    async with org_scoped_session(scene.org) as s:
        with pytest.raises(ApprovalExpired):
            await service.resolve(s, scene.org, aid, approver_user_id=scene.user,
                                  decision="approve")
