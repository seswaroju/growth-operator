"""Support-ticket API end-to-end against a real Postgres (support-tickets track).

Proves the loop the unit tests can't: an owner raises a ticket, only their org sees it, a
non-allowlisted caller is denied the operator queue, and an allowlisted operator sees every tenant,
resolves a ticket (owner then sees it resolved), with an audit-log entry written. Skips when the DB
is unreachable.
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


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.support_tickets')"))
    finally:
        await conn.close()


def _tok(user: uuid.UUID, org: uuid.UUID | None) -> str:
    return issue_access_token(sub=str(user), secret=get_settings().jwt_secret,
                             org_id=str(org) if org else None, roles=[ROLE_OWNER])


@dataclass
class Scene:
    client: httpx.AsyncClient
    org_a: uuid.UUID
    org_b: uuid.UUID
    user_a: uuid.UUID
    user_b: uuid.UUID
    admin: uuid.UUID

    def hdr(self, user: uuid.UUID, org: uuid.UUID | None) -> dict[str, str]:
        return {"Authorization": f"Bearer {_tok(user, org)}"}


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/support_tickets not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")  # operator plane on for tests
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    user_a, user_b, admin = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id,name) VALUES ($1,'Alpha'),($2,'Beta')",
                           org_a, org_b)
        await conn.execute("INSERT INTO users (id,phone) VALUES ($1,$2),($3,$4)",
                           user_a, f"+91{user_a.int % 10**10:010d}",
                           user_b, f"+91{user_b.int % 10**10:010d}")
        await conn.execute("INSERT INTO users (id,email) VALUES ($1,'ops@example.test')", admin)
        await conn.execute("INSERT INTO platform_admins (user_id) VALUES ($1)", admin)
    finally:
        await conn.close()
    from core.api.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Scene(client, org_a, org_b, user_a, user_b, admin)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id = ANY($1::uuid[])", [org_a, org_b])
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute(
            "ALTER TABLE platform_access_log DISABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute(
            "DELETE FROM platform_access_log WHERE actor_user_id=$1 "
            "OR target_org_id = ANY($2::uuid[])", admin, [org_a, org_b])
        await conn.execute(
            "ALTER TABLE platform_access_log ENABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM support_tickets WHERE org_id = ANY($1::uuid[])",
                           [org_a, org_b])
        await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", admin)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", [org_a, org_b])
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", [user_a, user_b, admin])
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _raise(scene: Scene, user: uuid.UUID, org: uuid.UUID, **over: object) -> httpx.Response:
    body = {"subject": "WhatsApp keeps disconnecting", "description": "Dropped twice today.",
            "category": "whatsapp", "severity": "major", **over}
    return await scene.client.post("/v1/support/tickets", headers=scene.hdr(user, org), json=body)


async def test_owner_raises_ticket_with_defaults(scene: Scene) -> None:
    r = await _raise(scene, scene.user_a, scene.org_a)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "open" and body["priority"] == "normal"  # operator triages priority
    assert "org_name" not in body  # owner view never leaks cross-tenant fields


async def test_owner_isolation(scene: Scene) -> None:
    await _raise(scene, scene.user_a, scene.org_a)
    ra = await scene.client.get("/v1/support/tickets", headers=scene.hdr(scene.user_a, scene.org_a))
    rb = await scene.client.get("/v1/support/tickets", headers=scene.hdr(scene.user_b, scene.org_b))
    assert len(ra.json()) == 1 and rb.json() == []  # B cannot see A's ticket


async def test_operator_queue_forbidden_for_non_admin(scene: Scene) -> None:
    r = await scene.client.get("/v1/admin/support/tickets",
                               headers=scene.hdr(scene.user_a, scene.org_a))
    assert r.status_code == 403  # owner is not on the platform_admins allowlist


async def test_operator_sees_all_tenants_and_resolves(scene: Scene) -> None:
    ra = await _raise(scene, scene.user_a, scene.org_a)
    await _raise(scene, scene.user_b, scene.org_b, subject="Catalog import failed",
                 severity="minor")
    ticket_a = ra.json()["id"]
    admin_hdr = scene.hdr(scene.admin, None)

    queue = (await scene.client.get("/v1/admin/support/tickets", headers=admin_hdr)).json()
    assert {t["org_name"] for t in queue} == {"Alpha", "Beta"}  # cross-tenant view

    rp = await scene.client.patch(
        f"/v1/admin/support/tickets/{ticket_a}", headers=admin_hdr,
        json={"status": "resolved", "priority": "high", "resolution_note": "Reconnected."})
    assert rp.status_code == 200, rp.text
    assert rp.json()["status"] == "resolved" and rp.json()["priority"] == "high"
    assert rp.json()["resolved_at"] is not None

    # owner sees the resolution (loop closed)
    owner_view = (await scene.client.get(
        f"/v1/support/tickets/{ticket_a}", headers=scene.hdr(scene.user_a, scene.org_a))).json()
    assert owner_view["status"] == "resolved" and owner_view["resolution_note"] == "Reconnected."

    # the operator action is audited in the tenant's chain
    conn = await asyncpg.connect(_dsn())
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM audit_log WHERE org_id=$1 AND action='support.ticket.updated'",
            scene.org_a)
    finally:
        await conn.close()
    assert n >= 1


async def test_operator_plane_hidden_when_disabled() -> None:
    # With admin_plane_enabled at its secure default (off — no monkeypatch here), the operator
    # endpoint 404s for EVERYONE: an unauthenticated caller AND a genuinely-allowlisted admin. The
    # admin API's existence is not revealed.
    if not await _db_ready():
        pytest.skip("Postgres/support_tickets not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    admin = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO users (id,email) VALUES ($1,'off@example.test')", admin)
        await conn.execute("INSERT INTO platform_admins (user_id) VALUES ($1)", admin)
    finally:
        await conn.close()
    try:
        from core.api.main import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            valid_admin = await client.get(
                "/v1/admin/support/tickets",
                headers={"Authorization": f"Bearer {_tok(admin, None)}"})
            unauth = await client.get("/v1/admin/support/tickets")
        assert valid_admin.status_code == 404  # even a real admin can't see a disabled plane
        assert unauth.status_code == 404       # existence hidden from anonymous callers
    finally:
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", admin)
            await conn.execute("DELETE FROM users WHERE id=$1", admin)
        finally:
            await conn.close()
        await dbmod.get_engine().dispose()
        dbmod.get_engine.cache_clear()
        dbmod.get_sessionmaker.cache_clear()


async def test_cross_tenant_read_and_write_are_logged(scene: Scene) -> None:
    # Every operator cross-tenant action lands in the append-only platform_access_log — READ (view
    # the queue) as well as WRITE (resolving), with the actor recorded.
    ra = await _raise(scene, scene.user_a, scene.org_a)
    admin_hdr = scene.hdr(scene.admin, None)
    await scene.client.get("/v1/admin/support/tickets", headers=admin_hdr)  # a cross-tenant READ
    await scene.client.patch(f"/v1/admin/support/tickets/{ra.json()['id']}",
                             headers=admin_hdr, json={"status": "resolved"})  # a cross-tenant WRITE
    conn = await asyncpg.connect(_dsn())
    try:
        reads = await conn.fetch(
            "SELECT detail FROM platform_access_log "
            "WHERE actor_user_id=$1 AND action='support.queue.viewed'", scene.admin)
        writes = await conn.fetch(
            "SELECT target_org_id FROM platform_access_log "
            "WHERE actor_user_id=$1 AND action='support.ticket.updated'", scene.admin)
    finally:
        await conn.close()
    assert len(reads) >= 1 and "count" in reads[0]["detail"]
    assert len(writes) >= 1 and writes[0]["target_org_id"] == scene.org_a


async def test_invalid_severity_is_rejected(scene: Scene) -> None:
    r = await _raise(scene, scene.user_a, scene.org_a, severity="apocalyptic")
    assert r.status_code == 422


async def test_empty_patch_is_rejected(scene: Scene) -> None:
    ra = await _raise(scene, scene.user_a, scene.org_a)
    r = await scene.client.patch(f"/v1/admin/support/tickets/{ra.json()['id']}",
                                 headers=scene.hdr(scene.admin, None), json={})
    assert r.status_code == 422  # must change at least one field
