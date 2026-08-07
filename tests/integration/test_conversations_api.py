"""Conversations & leads read endpoints against real Postgres (Phase 3, Ticket 3.3).

Proves the inbox (`GET /v1/conversations` — contact + last-message preview + count), the thread
(`GET /v1/conversations/{id}` — messages ascending, 404 cross-org), and the pipeline
(`GET /v1/leads`), all org-scoped (B never sees A) and gated by `conversations:read`. Skips when
the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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
        return bool(await conn.fetchval("SELECT to_regclass('public.leads')"))
    finally:
        await conn.close()


def _tok(user: uuid.UUID, org: uuid.UUID | None, roles: tuple[str, ...] = (ROLE_OWNER,)) -> str:
    return issue_access_token(sub=str(user), secret=get_settings().jwt_secret,
                             org_id=str(org) if org else None, roles=list(roles))


async def _seed(conn: asyncpg.Connection, org: uuid.UUID, *, name: str) -> uuid.UUID:
    """Seed one contact + channel + conversation (2 messages) + 2 leads. Returns conversation id."""
    ch = await conn.fetchval(
        "INSERT INTO channels (org_id,type,external_id,credentials_ref) "
        "VALUES ($1,'whatsapp',$2,'ref') RETURNING id", org, f"ext-{uuid.uuid4()}")
    ct = await conn.fetchval(
        "INSERT INTO contacts (org_id, phone, full_name) VALUES ($1,$2,$3) RETURNING id",
        org, f"+9199{org.int % 10**8:08d}", name)
    conv = await conn.fetchval(
        "INSERT INTO conversations (org_id, contact_id, channel_id, status) "
        "VALUES ($1,$2,$3,'open') RETURNING id", org, ct, ch)
    base = datetime.now(UTC) - timedelta(minutes=10)
    msg_sql = (
        "INSERT INTO messages "
        "(org_id, conversation_id, direction, sender, body, status, created_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7)"
    )
    await conn.execute(msg_sql, org, conv, "inbound", "customer",
                       "Do you have 22K gold rings?", "received", base)
    await conn.execute(msg_sql, org, conv, "outbound", "store",
                       "Yes! Here is our collection.", "sent", base + timedelta(minutes=2))
    for stage in ("new", "quoted"):
        await conn.execute(
            "INSERT INTO leads (org_id, contact_id, source, stage, intent, score) "
            "VALUES ($1,$2,'whatsapp',$3,'{}'::jsonb,50)", org, ct, stage)
    return conv


@dataclass
class Scene:
    client: httpx.AsyncClient
    org_a: uuid.UUID
    org_b: uuid.UUID
    user_a: uuid.UUID
    user_b: uuid.UUID
    conv_a: uuid.UUID
    conv_b: uuid.UUID

    def hdr(self, user: uuid.UUID, org: uuid.UUID | None,
            roles: tuple[str, ...] = (ROLE_OWNER,)) -> dict[str, str]:
        return {"Authorization": f"Bearer {_tok(user, org, roles)}"}


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/leads not ready")
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
        conv_a = await _seed(conn, org_a, name="Priya")
        conv_b = await _seed(conn, org_b, name="Ravi")
    finally:
        await conn.close()
    from core.api.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Scene(client, org_a, org_b, user_a, user_b, conv_a, conv_b)
    conn = await asyncpg.connect(_dsn())
    try:
        orgs = [org_a, org_b]
        await conn.execute("DELETE FROM messages WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM leads WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM conversations WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM contacts WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM channels WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", [user_a, user_b])
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_inbox_lists_conversation_with_last_message(scene: Scene) -> None:
    r = await scene.client.get("/v1/conversations", headers=scene.hdr(scene.user_a, scene.org_a))
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 1
    c = items[0]
    assert c["contact_name"] == "Priya" and c["message_count"] == 2
    assert c["last_message"]["direction"] == "outbound"  # most recent
    assert c["last_message"]["body"] == "Yes! Here is our collection."


async def test_inbox_is_org_scoped(scene: Scene) -> None:
    ra = await scene.client.get("/v1/conversations", headers=scene.hdr(scene.user_a, scene.org_a))
    rb = await scene.client.get("/v1/conversations", headers=scene.hdr(scene.user_b, scene.org_b))
    assert [c["contact_name"] for c in ra.json()] == ["Priya"]
    assert [c["contact_name"] for c in rb.json()] == ["Ravi"]  # B never sees Priya


async def test_thread_returns_messages_ascending(scene: Scene) -> None:
    r = await scene.client.get(f"/v1/conversations/{scene.conv_a}",
                               headers=scene.hdr(scene.user_a, scene.org_a))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["contact_name"] == "Priya"
    dirs = [m["direction"] for m in body["messages"]]
    assert dirs == ["inbound", "outbound"]  # chronological


async def test_thread_cross_org_is_404(scene: Scene) -> None:
    # A asks for B's conversation → not found (never leaks another tenant's resource).
    r = await scene.client.get(f"/v1/conversations/{scene.conv_b}",
                               headers=scene.hdr(scene.user_a, scene.org_a))
    assert r.status_code == 404


async def test_leads_pipeline(scene: Scene) -> None:
    r = await scene.client.get("/v1/leads", headers=scene.hdr(scene.user_a, scene.org_a))
    assert r.status_code == 200, r.text
    stages = sorted(lead["stage"] for lead in r.json())
    assert stages == ["new", "quoted"]
    assert all(lead["contact_name"] == "Priya" for lead in r.json())


async def test_conversations_forbidden_without_permission(scene: Scene) -> None:
    r = await scene.client.get("/v1/conversations",
                               headers=scene.hdr(scene.user_a, scene.org_a, roles=()))
    assert r.status_code == 403