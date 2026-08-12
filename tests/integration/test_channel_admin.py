"""Operator channel setup (CP-4) — `/v1/admin/tenants/{org_id}/channels`.

The GO operator wires a store's channels by pasting tokens; they're stored encrypted, one row per
(store, type), never returned or logged, and an account can't be wired to two stores. Operator-
gated. Rigorous corner-case coverage. Skips when the DB is unreachable.

Credential values below are obviously fake — never real tokens.
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
from core.common.crypto import decrypt_json
from core.tenancy.auth import issue_access_token

# Fake credential blobs per channel type (external-id field + a clearly-fake token).
IG_CREDS = {"ig_user_id": "17841400000000000", "access_token": "FAKE-ig-token-not-real"}
WA_CREDS = {"waba_id": "WABA_FAKE", "phone_number_id": "PN_FAKE_1", "access_token": "FAKE-wa-token"}
GOOGLE_CREDS = {"customer_id": "123-456-7890", "developer_token": "DEV_FAKE",
                "access_token": "FAKE-google-token"}


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.channel_credentials')"))
    finally:
        await conn.close()


def _op(user: uuid.UUID) -> dict[str, str]:
    token = issue_access_token(
        sub=str(user), secret=get_settings().jwt_secret, org_id=None, roles=[])
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Scene:
    client: httpx.AsyncClient
    operator: uuid.UUID
    org: uuid.UUID
    tag: str  # unique per test — org names carry it, for cleanup


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/channels not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    operator, org = uuid.uuid4(), uuid.uuid4()
    tag = operator.hex[:8]
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           operator, f"op+{tag}@example.test")
        await conn.execute("INSERT INTO platform_admins (user_id, role) VALUES ($1,'admin')",
                           operator)
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,$2)",
                           org, f"ChanStore-{tag}-A")
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, operator, org, tag)
    conn = await asyncpg.connect(_dsn())
    try:
        # Orgs cascade channels + channel_credentials. Clean by tag.
        await conn.execute("DELETE FROM organizations WHERE name LIKE $1", f"ChanStore-{tag}%")
        await conn.execute(
            "ALTER TABLE platform_access_log DISABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_access_log WHERE actor_user_id=$1", operator)
        await conn.execute(
            "ALTER TABLE platform_access_log ENABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", operator)
        await conn.execute("DELETE FROM users WHERE id=$1", operator)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _url(org: uuid.UUID) -> str:
    return f"/v1/admin/tenants/{org}/channels"


async def _make_org(tag: str, suffix: str) -> uuid.UUID:
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,$2)",
                           org, f"ChanStore-{tag}-{suffix}")
    finally:
        await conn.close()
    return org


@pytest.mark.parametrize(
    ("ctype", "creds", "eid"),
    [("instagram", IG_CREDS, "17841400000000000"),
     ("whatsapp", WA_CREDS, "PN_FAKE_1"),
     ("google_ads", GOOGLE_CREDS, "123-456-7890")])
async def test_connect_each_type_stores_encrypted(
    scene: Scene, ctype: str, creds: dict, eid: str) -> None:
    r = await scene.client.post(
        _url(scene.org), headers=_op(scene.operator), json={"type": ctype, "credentials": creds})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["type"] == ctype and body["external_id"] == eid and body["status"] == "active"

    conn = await asyncpg.connect(_dsn())
    try:
        # one channel row of this type on the store
        row = await conn.fetchrow(
            "SELECT id, external_id, status FROM channels WHERE org_id=$1 AND type=$2",
            scene.org, ctype)
        assert row is not None and row["external_id"] == eid and row["status"] == "active"
        # credentials are stored ENCRYPTED — the raw token never appears in the ciphertext, but it
        # round-trips through decryption to exactly what was pasted.
        ciphertext = await conn.fetchval(
            "SELECT ciphertext FROM channel_credentials WHERE channel_id=$1", row["id"])
        assert ciphertext is not None
        assert creds["access_token"] not in ciphertext  # not stored in the clear
        assert decrypt_json(ciphertext) == creds
    finally:
        await conn.close()


async def test_list_never_returns_credentials(scene: Scene) -> None:
    await scene.client.post(
        _url(scene.org), headers=_op(scene.operator),
        json={"type": "instagram", "credentials": IG_CREDS})
    r = await scene.client.get(_url(scene.org), headers=_op(scene.operator))
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 1
    assert items[0]["type"] == "instagram" and items[0]["external_id"] == "17841400000000000"
    # the response body must not carry any credential value
    assert "access_token" not in r.text and "FAKE-ig-token-not-real" not in r.text


async def test_missing_required_field_is_422_and_writes_nothing(scene: Scene) -> None:
    r = await scene.client.post(
        _url(scene.org), headers=_op(scene.operator),
        json={"type": "instagram", "credentials": {"ig_user_id": "17841400000000000"}})  # no token
    assert r.status_code == 422
    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval(
            "SELECT count(*) FROM channels WHERE org_id=$1", scene.org) == 0
    finally:
        await conn.close()


async def test_unknown_type_is_422(scene: Scene) -> None:
    r = await scene.client.post(
        _url(scene.org), headers=_op(scene.operator),
        json={"type": "carrier_pigeon", "credentials": {"x": "y"}})
    assert r.status_code == 422


async def test_repaste_updates_in_place(scene: Scene) -> None:
    op = _op(scene.operator)
    r1 = await scene.client.post(
        _url(scene.org), headers=op, json={"type": "instagram", "credentials": IG_CREDS})
    new_creds = {"ig_user_id": "17841499999999999", "access_token": "FAKE-ig-token-v2"}
    r2 = await scene.client.post(
        _url(scene.org), headers=op, json={"type": "instagram", "credentials": new_creds})
    assert r1.status_code == 201 and r2.status_code == 201
    conn = await asyncpg.connect(_dsn())
    try:  # still ONE instagram channel — re-paste replaced, not appended
        n = await conn.fetchval(
            "SELECT count(*) FROM channels WHERE org_id=$1 AND type='instagram'", scene.org)
        assert n == 1
        eid = await conn.fetchval(
            "SELECT external_id FROM channels WHERE org_id=$1 AND type='instagram'", scene.org)
        assert eid == "17841499999999999"
    finally:
        await conn.close()


async def test_disconnect_removes_channel_and_credentials(scene: Scene) -> None:
    op = _op(scene.operator)
    cid = (await scene.client.post(
        _url(scene.org), headers=op,
        json={"type": "instagram", "credentials": IG_CREDS})).json()["channel_id"]
    d = await scene.client.delete(f"{_url(scene.org)}/{cid}", headers=op)
    assert d.status_code == 204
    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval("SELECT count(*) FROM channels WHERE id=$1", cid) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM channel_credentials WHERE channel_id=$1", cid) == 0
    finally:
        await conn.close()


async def test_disconnect_unknown_channel_is_404(scene: Scene) -> None:
    d = await scene.client.delete(
        f"{_url(scene.org)}/{uuid.uuid4()}", headers=_op(scene.operator))
    assert d.status_code == 404


async def test_same_account_on_another_store_is_409(scene: Scene) -> None:
    op = _op(scene.operator)
    r1 = await scene.client.post(
        _url(scene.org), headers=op, json={"type": "instagram", "credentials": IG_CREDS})
    assert r1.status_code == 201
    other = await _make_org(scene.tag, "B")  # cleaned by the tag LIKE
    r2 = await scene.client.post(
        _url(other), headers=op, json={"type": "instagram", "credentials": IG_CREDS})
    assert r2.status_code == 409  # the same IG account can't be wired to two stores
    # and org B got nothing
    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval("SELECT count(*) FROM channels WHERE org_id=$1", other) == 0
    finally:
        await conn.close()


async def test_non_operator_is_403(scene: Scene) -> None:
    stranger = uuid.uuid4()  # valid token but not an allowlisted operator
    r = await scene.client.post(
        _url(scene.org), headers=_op(stranger),
        json={"type": "instagram", "credentials": IG_CREDS})
    assert r.status_code == 403


async def test_plane_disabled_is_404(scene: Scene, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "false")
    r = await scene.client.post(
        _url(scene.org), headers=_op(scene.operator),
        json={"type": "instagram", "credentials": IG_CREDS})
    assert r.status_code == 404


async def test_channel_types_lists_the_registry(scene: Scene) -> None:
    r = await scene.client.get(
        "/v1/admin/tenants/channel-types", headers=_op(scene.operator))
    assert r.status_code == 200, r.text
    types = {t["type"] for t in r.json()}
    assert {"whatsapp", "instagram", "google_ads"} <= types
    ig = next(t for t in r.json() if t["type"] == "instagram")
    assert ig["external_id_field"] == "ig_user_id"
    assert "access_token" in ig["credential_fields"]
