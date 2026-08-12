"""Operator broadcasts / announcements (CP-7).

The operator publishes an announcement (`/v1/admin/announcements`) and EVERY store's owner sees it
in their notification feed (`/v1/notifications`) — the "blast to all stores". Archiving retracts it.
Operator-gated. Corner-case coverage. Skips when the DB is unreachable.
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


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.announcements')"))
    finally:
        await conn.close()


def _op(user: uuid.UUID) -> dict[str, str]:
    token = issue_access_token(
        sub=str(user), secret=get_settings().jwt_secret, org_id=None, roles=[])
    return {"Authorization": f"Bearer {token}"}


def _owner(user: uuid.UUID, org: uuid.UUID) -> dict[str, str]:
    token = issue_access_token(
        sub=str(user), secret=get_settings().jwt_secret, org_id=str(org), roles=["owner"])
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Scene:
    client: httpx.AsyncClient
    operator: uuid.UUID
    org_a: uuid.UUID
    owner_a: uuid.UUID
    org_b: uuid.UUID
    owner_b: uuid.UUID
    tag: str


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/announcements not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    operator = uuid.uuid4()
    org_a, owner_a, org_b, owner_b = (uuid.uuid4() for _ in range(4))
    tag = operator.hex[:8]
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           operator, f"op+{tag}@example.test")
        await conn.execute("INSERT INTO platform_admins (user_id, role) VALUES ($1,'admin')",
                           operator)
        for org, owner, suffix in ((org_a, owner_a, "A"), (org_b, owner_b, "B")):
            await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,$2)",
                               org, f"AnnStore-{tag}-{suffix}")
            await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                               owner, f"own+{owner.hex[:8]}@example.test")
            await conn.execute(
                "INSERT INTO user_orgs (user_id, org_id, role) VALUES ($1,$2,'owner')", owner, org)
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, operator, org_a, owner_a, org_b, owner_b, tag)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM announcements WHERE title LIKE $1", f"Ann-{tag}%")
        await conn.execute("DELETE FROM organizations WHERE name LIKE $1", f"AnnStore-{tag}%")
        await conn.execute(
            "ALTER TABLE platform_access_log DISABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_access_log WHERE actor_user_id=$1", operator)
        await conn.execute(
            "ALTER TABLE platform_access_log ENABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", operator)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])",
                           [operator, owner_a, owner_b])
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _publish(scene: Scene, title: str, body: str = "Body", level: str = "update"):
    return await scene.client.post(
        "/v1/admin/announcements", headers=_op(scene.operator),
        json={"title": title, "body": body, "level": level})


async def _feed_titles(scene: Scene, owner: uuid.UUID, org: uuid.UUID) -> list[str]:
    r = await scene.client.get("/v1/notifications", headers=_owner(owner, org))
    assert r.status_code == 200, r.text
    return [i["title"] for i in r.json()["items"] if i["kind"] == "announcement"]


async def test_publish_reaches_every_store_owner(scene: Scene) -> None:
    title = f"Ann-{scene.tag}-launch"
    r = await _publish(scene, title, body="New WhatsApp tier from Sept 1", level="update")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == title and body["level"] == "update" and body["archived_at"] is None

    # BOTH stores' owners see it — that's the broadcast.
    assert title in await _feed_titles(scene, scene.owner_a, scene.org_a)
    assert title in await _feed_titles(scene, scene.owner_b, scene.org_b)
    # the owner feed carries the body + level for display
    r = await scene.client.get("/v1/notifications", headers=_owner(scene.owner_a, scene.org_a))
    ann = next(i for i in r.json()["items"] if i["kind"] == "announcement")
    assert ann["body"] == "New WhatsApp tier from Sept 1" and ann["level"] == "update"


async def test_archive_removes_it_from_feeds(scene: Scene) -> None:
    title = f"Ann-{scene.tag}-temp"
    aid = (await _publish(scene, title)).json()["id"]
    assert title in await _feed_titles(scene, scene.owner_a, scene.org_a)

    d = await scene.client.post(
        f"/v1/admin/announcements/{aid}/archive", headers=_op(scene.operator))
    assert d.status_code == 204
    assert title not in await _feed_titles(scene, scene.owner_a, scene.org_a)  # gone from the feed
    # still visible to the operator's management list, now archived
    lst = await scene.client.get("/v1/admin/announcements", headers=_op(scene.operator))
    row = next(a for a in lst.json() if a["id"] == aid)
    assert row["archived_at"] is not None


async def test_archive_unknown_is_404(scene: Scene) -> None:
    r = await scene.client.post(
        f"/v1/admin/announcements/{uuid.uuid4()}/archive", headers=_op(scene.operator))
    assert r.status_code == 404


async def test_operator_list_shows_published(scene: Scene) -> None:
    title = f"Ann-{scene.tag}-listed"
    await _publish(scene, title)
    lst = await scene.client.get("/v1/admin/announcements", headers=_op(scene.operator))
    assert lst.status_code == 200
    assert title in {a["title"] for a in lst.json()}


async def test_invalid_level_is_422(scene: Scene) -> None:
    r = await _publish(scene, f"Ann-{scene.tag}-bad", level="urgent")  # not in the enum
    assert r.status_code == 422


async def test_empty_title_is_422(scene: Scene) -> None:
    r = await scene.client.post(
        "/v1/admin/announcements", headers=_op(scene.operator),
        json={"title": "", "body": "x"})
    assert r.status_code == 422


async def test_non_operator_cannot_publish(scene: Scene) -> None:
    r = await scene.client.post(
        "/v1/admin/announcements", headers=_op(uuid.uuid4()),
        json={"title": f"Ann-{scene.tag}-x", "body": "x"})
    assert r.status_code == 403


async def test_plane_disabled_is_404(scene: Scene, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "false")
    r = await scene.client.get("/v1/admin/announcements", headers=_op(scene.operator))
    assert r.status_code == 404
