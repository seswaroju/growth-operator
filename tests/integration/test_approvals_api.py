"""Approvals queue HTTP API against real Postgres (Phase 3, Ticket 3.2).

Proves the owner-facing queue the customer app consumes: `GET /v1/approvals` lists pending items
(with the `matched_rules` "why", org-scoped so B never sees A's, role-gated), and
`POST /v1/approvals/{id}/resolve` approves / rejects, 410s an expired item, 404s an unknown one.
Skips when the DB is unreachable.
"""

from __future__ import annotations

import json
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


def _tok(user: uuid.UUID, org: uuid.UUID | None, roles: tuple[str, ...] = (ROLE_OWNER,)) -> str:
    return issue_access_token(sub=str(user), secret=get_settings().jwt_secret,
                             org_id=str(org) if org else None, roles=list(roles))


async def _mk_approval(conn: asyncpg.Connection, org: uuid.UUID, *, body: str,
                       rules: list[str], expires_min: int = 60) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO approvals (org_id, action_type, tier, payload, matched_rules, expires_at) "
        "VALUES ($1,'messages.send',2, $2::jsonb, $3::jsonb, now() + ($4 || ' minutes')::interval) "
        "RETURNING id",
        org, json.dumps({"body": body, "conversation_id": str(uuid.uuid4()),
                         "message_class": "transactional"}), json.dumps(rules), str(expires_min))


@dataclass
class Scene:
    client: httpx.AsyncClient
    org_a: uuid.UUID
    org_b: uuid.UUID
    user_a: uuid.UUID
    user_b: uuid.UUID

    def hdr(self, user: uuid.UUID, org: uuid.UUID | None,
            roles: tuple[str, ...] = (ROLE_OWNER,)) -> dict[str, str]:
        return {"Authorization": f"Bearer {_tok(user, org, roles)}"}


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/approvals not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id,name) VALUES ($1,'Alpha'),($2,'Beta')",
                           org_a, org_b)
        await conn.execute("INSERT INTO users (id,email) VALUES ($1,$2),($3,$4)",
                           user_a, f"{user_a}@example.test", user_b, f"{user_b}@example.test")
    finally:
        await conn.close()
    from core.api.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Scene(client, org_a, org_b, user_a, user_b)
    conn = await asyncpg.connect(_dsn())
    try:
        orgs = [org_a, org_b]
        await conn.execute("DELETE FROM event_outbox WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM approvals WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", [user_a, user_b])
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_queue_lists_pending_with_matched_rules(scene: Scene) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await _mk_approval(conn, scene.org_a, body="Namaste! Here's your ring.",
                           rules=["messages.send:tier2", "quote_over_threshold"])
    finally:
        await conn.close()
    r = await scene.client.get("/v1/approvals", headers=scene.hdr(scene.user_a, scene.org_a))
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 1
    item = items[0]
    assert item["action_type"] == "messages.send" and item["tier"] == 2
    assert item["payload"]["body"] == "Namaste! Here's your ring."
    assert item["matched_rules"] == ["messages.send:tier2", "quote_over_threshold"]


async def test_queue_is_org_scoped(scene: Scene) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await _mk_approval(conn, scene.org_a, body="A's draft", rules=["r"])
        await _mk_approval(conn, scene.org_b, body="B's draft", rules=["r"])
    finally:
        await conn.close()
    ra = await scene.client.get("/v1/approvals", headers=scene.hdr(scene.user_a, scene.org_a))
    rb = await scene.client.get("/v1/approvals", headers=scene.hdr(scene.user_b, scene.org_b))
    assert [i["payload"]["body"] for i in ra.json()] == ["A's draft"]  # A never sees B's
    assert [i["payload"]["body"] for i in rb.json()] == ["B's draft"]


async def test_approve_marks_approved(scene: Scene) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        aid = await _mk_approval(conn, scene.org_a, body="Reply", rules=["r"])
    finally:
        await conn.close()
    r = await scene.client.post(f"/v1/approvals/{aid}/resolve",
                                headers=scene.hdr(scene.user_a, scene.org_a),
                                json={"decision": "approve"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved" and r.json()["idempotent_replay"] is False


async def test_reject_marks_rejected(scene: Scene) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        aid = await _mk_approval(conn, scene.org_a, body="Reply", rules=["r"])
    finally:
        await conn.close()
    r = await scene.client.post(f"/v1/approvals/{aid}/resolve",
                                headers=scene.hdr(scene.user_a, scene.org_a),
                                json={"decision": "reject", "note": "Wrong price"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"


async def test_resolve_expired_returns_410(scene: Scene) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        aid = await _mk_approval(conn, scene.org_a, body="Stale", rules=["r"], expires_min=-1)
    finally:
        await conn.close()
    r = await scene.client.post(f"/v1/approvals/{aid}/resolve",
                                headers=scene.hdr(scene.user_a, scene.org_a),
                                json={"decision": "approve"})
    assert r.status_code == 410


async def test_resolve_unknown_returns_404(scene: Scene) -> None:
    r = await scene.client.post(f"/v1/approvals/{uuid.uuid4()}/resolve",
                                headers=scene.hdr(scene.user_a, scene.org_a),
                                json={"decision": "approve"})
    assert r.status_code == 404


async def test_queue_forbidden_without_approvals_read(scene: Scene) -> None:
    r = await scene.client.get("/v1/approvals",
                               headers=scene.hdr(scene.user_a, scene.org_a, roles=()))
    assert r.status_code == 403
