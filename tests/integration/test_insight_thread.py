"""Owner⇄GO insight thread + its split-RLS teeth against real Postgres (Phase 3.5-eng, A4.5).

Proves: the owner asks, the operator answers cross-tenant, the owner sees both; an owner can't read
another org's thread; **an owner cannot forge an operator-authored message** (RLS WITH CHECK); a
non-operator is refused the reply endpoint. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg
import httpx
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.auth import issue_access_token
from core.tenancy.permissions import ROLE_OWNER
from core.tenancy.repository import set_org_context


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.insight_messages')"))
    finally:
        await conn.close()


def _tok(user: uuid.UUID, org: uuid.UUID | None, roles: tuple[str, ...] = (ROLE_OWNER,)) -> str:
    return issue_access_token(sub=str(user), secret=get_settings().jwt_secret,
                             org_id=str(org) if org else None, roles=list(roles))


async def _mk_report(conn: asyncpg.Connection, org: uuid.UUID) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO agent_reports (org_id, report_type, title, verdict) "
        "VALUES ($1,'campaign_analysis','Diwali','worked') RETURNING id", org)


@dataclass
class Scene:
    client: httpx.AsyncClient
    org_a: uuid.UUID
    org_b: uuid.UUID
    user_a: uuid.UUID
    admin: uuid.UUID
    report_a: uuid.UUID
    report_b: uuid.UUID

    def owner(self, org: uuid.UUID) -> dict[str, str]:
        u = self.user_a if org == self.org_a else uuid.uuid4()
        return {"Authorization": f"Bearer {_tok(u, org)}"}

    def op(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {_tok(self.admin, None)}"}


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/insight_messages not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    user_a, admin = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id,name) VALUES ($1,'Alpha'),($2,'Beta')",
                           org_a, org_b)
        await conn.execute("INSERT INTO users (id,email) VALUES ($1,$2)",
                           user_a, f"{user_a}@example.test")
        await conn.execute("INSERT INTO users (id,email) VALUES ($1,'ops@example.test')", admin)
        await conn.execute("INSERT INTO platform_admins (user_id) VALUES ($1)", admin)
        report_a = await _mk_report(conn, org_a)
        report_b = await _mk_report(conn, org_b)
    finally:
        await conn.close()
    from core.api.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Scene(client, org_a, org_b, user_a, admin, report_a, report_b)
    conn = await asyncpg.connect(_dsn())
    try:
        orgs = [org_a, org_b]
        await conn.execute("DELETE FROM insight_messages WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM agent_reports WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute(
            "ALTER TABLE platform_access_log DISABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_access_log WHERE actor_user_id=$1", admin)
        await conn.execute(
            "ALTER TABLE platform_access_log ENABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", admin)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", [user_a, admin])
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_owner_asks_operator_answers(scene: Scene) -> None:
    q = await scene.client.post(f"/v1/insights/reports/{scene.report_a}/messages",
                                headers=scene.owner(scene.org_a),
                                json={"body": "How is ₹1.8L attributed to this?"})
    assert q.status_code == 201 and q.json()["author_type"] == "owner"
    a = await scene.client.post(
        f"/v1/admin/insights/reports/{scene.report_a}/reply",
        headers=scene.op(), json={"body": "First-touch: 12 leads → 2 sales."})
    assert a.status_code == 201, a.text
    assert a.json()["author_type"] == "operator"
    # the owner sees the full thread, oldest first
    thread = await scene.client.get(f"/v1/insights/reports/{scene.report_a}/messages",
                                    headers=scene.owner(scene.org_a))
    assert [m["author_type"] for m in thread.json()] == ["owner", "operator"]


async def test_owner_cannot_read_another_orgs_thread(scene: Scene) -> None:
    r = await scene.client.get(f"/v1/insights/reports/{scene.report_b}/messages",
                               headers=scene.owner(scene.org_a))
    assert r.status_code == 404  # report_b isn't org A's → not found


async def test_owner_cannot_forge_an_operator_message(scene: Scene) -> None:
    # A direct owner-context write (no platform flag) claiming author_type='operator' must be
    # rejected by the split-RLS WITH CHECK — the teeth of the cross-tenant boundary.
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        await set_org_context(s, scene.org_a)
        with pytest.raises(DBAPIError):
            await s.execute(
                text("INSERT INTO insight_messages (org_id, report_id, author_type, body) "
                     "VALUES (:o, :r, 'operator', 'forged')"),
                {"o": str(scene.org_a), "r": str(scene.report_a)})
            await s.commit()


async def test_reply_forbidden_for_non_operator(scene: Scene) -> None:
    # A store owner (not on the platform_admins allowlist) cannot use the operator reply endpoint.
    r = await scene.client.post(f"/v1/admin/insights/reports/{scene.report_a}/reply",
                                headers=scene.owner(scene.org_a), json={"body": "nope"})
    assert r.status_code == 403
