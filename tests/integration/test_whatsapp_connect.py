"""WhatsApp WABA connect flow (MVP-031) against real Postgres under app_rw.

Exercises the three connect gates (token / handshake / echo), that a channel row + an
**encrypted** credential row are written only on full success, that credentials are stored
as ciphertext (never plaintext) and never logged, reconnect-in-place, cross-org rejection,
and the health probe. The Meta client runs simulated (whatsapp_live_enabled=False), and
failure paths are driven either by simulated sentinels or by monkeypatching the client.
Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from fastapi import HTTPException
from sqlalchemy import text

from core.channels.whatsapp import connect as connect_mod
from core.channels.whatsapp.connect import ConnectRequest, connect, health
from core.channels.whatsapp.meta_client import MetaClient
from core.common import db as dbmod
from core.common.config import get_settings
from core.common.crypto import decrypt_json
from core.tenancy.deps import CurrentAuth


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


@pytest.fixture()
async def orgs() -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    if not await _db_ready():
        pytest.skip("Postgres/channel_credentials (cfd462c65ec9) not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    a, b = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'A'),($2,'B')", a, b)
    finally:
        await conn.close()
    yield a, b
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", [a, b])
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _auth(org: uuid.UUID) -> CurrentAuth:
    return CurrentAuth(user_id=uuid.uuid4(), org_id=org, roles=["owner"])


async def _run_connect(org: uuid.UUID, body: ConnectRequest) -> object:
    """Open a tenant-scoped session (as get_db would) and run the connect handler."""
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        await s.execute(text("SELECT set_config('app.org_id', :v, true)"), {"v": str(org)})
        resp = await connect(body, current=_auth(org), session=s)
        await s.commit()
    return resp


def _body(pnid: str, *, token: str = "tok-valid", waba: str = "waba-1") -> ConnectRequest:
    return ConnectRequest(waba_id=waba, phone_number_id=pnid, access_token=token)


async def test_connect_success_persists_channel_and_encrypted_credential(
    orgs: tuple[uuid.UUID, uuid.UUID],
) -> None:
    org, _ = orgs
    pnid = f"pn-{org.hex[:8]}"
    secret = "tok-super-secret-value"  # noqa: S105 - fake test token
    resp = await _run_connect(org, _body(pnid, token=secret))

    assert resp.connected is True
    assert resp.echo_ok is True and resp.webhook_registered is True
    assert resp.simulated is True
    assert resp.channel_id is not None

    conn = await asyncpg.connect(_dsn())
    try:
        chan = await conn.fetchrow(
            "SELECT org_id, status, credentials_ref FROM channels WHERE id=$1", resp.channel_id
        )
        assert chan["org_id"] == org and chan["status"] == "active"
        ciphertext = await conn.fetchval(
            "SELECT ciphertext FROM channel_credentials WHERE channel_id=$1", resp.channel_id
        )
    finally:
        await conn.close()
    # Stored as ciphertext — the plaintext token must not appear at rest…
    assert secret not in ciphertext
    # …but decrypts back to exactly what we supplied.
    assert decrypt_json(ciphertext) == {
        "waba_id": "waba-1", "phone_number_id": pnid, "access_token": secret,
    }


async def test_bad_token_returns_400_and_writes_no_row(
    orgs: tuple[uuid.UUID, uuid.UUID],
) -> None:
    org, _ = orgs
    pnid = f"pn-{org.hex[:8]}"
    with pytest.raises(HTTPException) as ei:
        await _run_connect(org, _body(pnid, token="invalid"))  # simulated → verify fails
    assert ei.value.status_code == 400 and ei.value.detail == "invalid_token"

    conn = await asyncpg.connect(_dsn())
    try:
        n = await conn.fetchval("SELECT count(*) FROM channels WHERE external_id=$1", pnid)
    finally:
        await conn.close()
    assert n == 0


async def test_handshake_mismatch_returns_403_and_writes_no_row(
    orgs: tuple[uuid.UUID, uuid.UUID], monkeypatch: pytest.MonkeyPatch,
) -> None:
    org, _ = orgs
    pnid = f"pn-{org.hex[:8]}"

    async def _no_webhook(self: MetaClient, waba_id: str, access_token: str) -> bool:
        return False

    monkeypatch.setattr(MetaClient, "register_webhook", _no_webhook)
    with pytest.raises(HTTPException) as ei:
        await _run_connect(org, _body(pnid))
    assert ei.value.status_code == 403 and ei.value.detail == "handshake_failed"

    conn = await asyncpg.connect(_dsn())
    try:
        n = await conn.fetchval("SELECT count(*) FROM channels WHERE external_id=$1", pnid)
    finally:
        await conn.close()
    assert n == 0


async def test_echo_failure_reports_not_connected_and_writes_no_row(
    orgs: tuple[uuid.UUID, uuid.UUID],
) -> None:
    org, _ = orgs
    resp = await _run_connect(org, _body("echo-fail"))  # simulated sentinel → echo fails
    assert resp.connected is False and resp.echo_ok is False
    assert resp.reason == "echo_failed"

    conn = await asyncpg.connect(_dsn())
    try:
        n = await conn.fetchval("SELECT count(*) FROM channels WHERE external_id='echo-fail'")
    finally:
        await conn.close()
    assert n == 0


async def test_credentials_never_logged(
    orgs: tuple[uuid.UUID, uuid.UUID], caplog: pytest.LogCaptureFixture,
) -> None:
    org, _ = orgs
    pnid = f"pn-{org.hex[:8]}"
    secret = "tok-do-not-log-me"  # noqa: S105 - fake test token
    with caplog.at_level("DEBUG"):
        await _run_connect(org, _body(pnid, token=secret))
    assert secret not in caplog.text


async def test_reconnect_same_org_updates_in_place(
    orgs: tuple[uuid.UUID, uuid.UUID],
) -> None:
    org, _ = orgs
    pnid = f"pn-{org.hex[:8]}"
    first = await _run_connect(org, _body(pnid, token="tok-1"))
    second = await _run_connect(org, _body(pnid, token="tok-2"))
    assert first.channel_id == second.channel_id

    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetchval("SELECT count(*) FROM channels WHERE external_id=$1", pnid)
        ciphertext = await conn.fetchval(
            "SELECT ciphertext FROM channel_credentials WHERE channel_id=$1", first.channel_id
        )
    finally:
        await conn.close()
    assert rows == 1  # updated, not duplicated
    assert decrypt_json(ciphertext)["access_token"] == "tok-2"  # credential rotated


async def test_number_owned_by_another_org_is_rejected(
    orgs: tuple[uuid.UUID, uuid.UUID],
) -> None:
    org_a, org_b = orgs
    pnid = f"pn-{org_a.hex[:8]}"
    await _run_connect(org_a, _body(pnid))
    with pytest.raises(HTTPException) as ei:
        await _run_connect(org_b, _body(pnid))
    assert ei.value.status_code == 409


async def test_health_probe(orgs: tuple[uuid.UUID, uuid.UUID]) -> None:
    org, _ = orgs
    pnid = f"pn-{org.hex[:8]}"
    connected = await _run_connect(org, _body(pnid))

    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        await s.execute(text("SELECT set_config('app.org_id', :v, true)"), {"v": str(org)})
        h = await health(connected.channel_id, current=_auth(org), session=s)
    assert h.healthy is True and h.status == "active"

    # Unknown channel → 404.
    async with factory() as s:
        await s.execute(text("SELECT set_config('app.org_id', :v, true)"), {"v": str(org)})
        with pytest.raises(HTTPException) as ei:
            await health(uuid.uuid4(), current=_auth(org), session=s)
    assert ei.value.status_code == 404


def test_module_imports_clean() -> None:
    assert connect_mod.router.prefix == "/v1/channels/whatsapp"
