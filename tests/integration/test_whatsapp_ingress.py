"""WhatsApp webhook ingress (MVP-032) against a real Postgres.

Covers the verify handshake, constant-time signature check, dedupe by wamid, and malformed
→ quarantine (always 200 after a valid signature). Skips when the DB is unreachable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest

from core.common import db as dbmod
from core.common.config import get_settings


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.webhook_events')"))
    finally:
        await conn.close()


def _sign(raw: bytes) -> str:
    secret = get_settings().whatsapp_app_secret.encode()
    return "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()


def _message_payload(wamid: str) -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [{"id": "waba1", "changes": [
                {"field": "messages", "value": {"messages": [
                    {"id": wamid, "from": "15551234567", "text": {"body": "hi"}}
                ]}}
            ]}],
        }
    ).encode()


async def _row_count(external_id: str) -> int:
    conn = await asyncpg.connect(_dsn())
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM webhook_events WHERE provider='whatsapp' AND external_id=$1",
            external_id,
        )
        return int(n)
    finally:
        await conn.close()


@pytest.fixture()
async def api() -> AsyncIterator[httpx.AsyncClient]:
    if not await _db_ready():
        pytest.skip("Postgres/migration 005 not ready")
    from core.api.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM webhook_events WHERE provider='whatsapp'")
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_verify_handshake(api: httpx.AsyncClient) -> None:
    token = get_settings().whatsapp_verify_token
    ok = await api.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "42"},
    )
    assert ok.status_code == 200 and ok.text == "42"
    bad = await api.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "42"},
    )
    assert bad.status_code == 403


async def test_valid_signature_persists_and_dedupes(api: httpx.AsyncClient) -> None:
    wamid = f"wamid.{uuid.uuid4().hex}"
    raw = _message_payload(wamid)
    headers = {"X-Hub-Signature-256": _sign(raw), "content-type": "application/json"}

    r1 = await api.post("/webhooks/whatsapp", content=raw, headers=headers)
    assert r1.status_code == 200
    assert await _row_count(wamid) == 1

    # A Meta retry (same wamid) → still one row.
    r2 = await api.post("/webhooks/whatsapp", content=raw, headers=headers)
    assert r2.status_code == 200
    assert await _row_count(wamid) == 1


async def test_invalid_signature_rejected_no_row(api: httpx.AsyncClient) -> None:
    wamid = f"wamid.{uuid.uuid4().hex}"
    raw = _message_payload(wamid)
    r = await api.post(
        "/webhooks/whatsapp", content=raw,
        headers={"X-Hub-Signature-256": "sha256=deadbeef", "content-type": "application/json"},
    )
    assert r.status_code == 403
    assert await _row_count(wamid) == 0  # nothing persisted


async def test_malformed_body_is_quarantined_with_200(api: httpx.AsyncClient) -> None:
    raw = b"{not json"
    r = await api.post(
        "/webhooks/whatsapp", content=raw,
        headers={"X-Hub-Signature-256": _sign(raw), "content-type": "application/json"},
    )
    assert r.status_code == 200  # never 5xx to Meta
    qid = f"malformed:{hashlib.sha256(raw).hexdigest()[:24]}"
    assert await _row_count(qid) == 1
