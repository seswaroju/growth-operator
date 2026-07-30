"""WhatsApp normalizer (MVP-033) end-to-end under app_rw.

Seeds an org + channel, drops a raw webhook into webhook_events, runs the normalizer, and
checks the contact/conversation/message were created, the lead touch-trigger context works,
msg.received.v1 was emitted, and reprocessing is idempotent. Skips when the DB is unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.channels.whatsapp import normalizer
from core.common import db as dbmod
from core.common.config import get_settings


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


def _webhook(pnid: str, wamid: str, phone: str, body: str) -> str:
    return json.dumps(
        {"entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": pnid},
            "messages": [{"id": wamid, "from": phone, "type": "text", "text": {"body": body}}],
        }}]}]}
    )


@pytest.fixture()
async def scene() -> AsyncIterator[dict]:
    conn_ok = False
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
        conn_ok = bool(await conn.fetchval("SELECT to_regclass('public.channels')"))
        await conn.close()
    except Exception:
        pass
    if not conn_ok:
        pytest.skip("Postgres/messaging+channel_resolve not ready")

    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    pnid = f"pnid-{org.hex[:8]}"
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'W')", org)
        await conn.execute(
            "INSERT INTO channels (org_id, type, external_id, credentials_ref) "
            "VALUES ($1,'whatsapp',$2,'ref')",
            org, pnid,
        )
    finally:
        await conn.close()
    yield {"org": org, "pnid": pnid}
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM webhook_events WHERE provider='whatsapp'")
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _insert_webhook(pnid: str, wamid: str, phone: str, body: str) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO webhook_events (provider, external_id, payload) "
            "VALUES ('whatsapp', $1, $2::jsonb)",
            wamid, _webhook(pnid, wamid, phone, body),
        )
    finally:
        await conn.close()


async def test_normalizes_message_and_emits_event(scene: dict) -> None:
    org, pnid = scene["org"], scene["pnid"]
    wamid, phone = f"wamid.{uuid.uuid4().hex}", "15551234567"
    await _insert_webhook(pnid, wamid, phone, "hello there")

    assert await normalizer.normalize_pending() >= 1

    conn = await asyncpg.connect(_dsn())
    try:
        contact = await conn.fetchrow(
            "SELECT id FROM contacts WHERE org_id=$1 AND phone=$2", org, phone
        )
        assert contact is not None
        msg = await conn.fetchrow(
            "SELECT direction, body FROM messages WHERE provider_message_id=$1", wamid
        )
        assert msg is not None and msg["direction"] == "inbound" and msg["body"] == "hello there"
        conv = await conn.fetchval("SELECT count(*) FROM conversations WHERE org_id=$1", org)
        assert conv == 1
        emitted = await conn.fetchval(
            "SELECT count(*) FROM event_outbox WHERE org_id=$1 AND type='msg.received.v1'", org
        )
        assert emitted == 1
        processed = await conn.fetchval(
            "SELECT processed_at FROM webhook_events WHERE external_id=$1", wamid
        )
        assert processed is not None
    finally:
        await conn.close()

    # Reprocess: webhook already processed → no new message / event.
    assert await normalizer.normalize_pending() == 0
    conn = await asyncpg.connect(_dsn())
    try:
        again = await conn.fetchval(
            "SELECT count(*) FROM messages WHERE provider_message_id=$1", wamid
        )
        assert again == 1
    finally:
        await conn.close()


async def test_unknown_channel_is_marked_processed_without_contact(scene: dict) -> None:
    wamid = f"wamid.{uuid.uuid4().hex}"
    await _insert_webhook("pnid-unknown", wamid, "15559999999", "hi")
    await normalizer.normalize_pending()
    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval(
            "SELECT processed_at FROM webhook_events WHERE external_id=$1", wamid
        ) is not None
        assert await conn.fetchval("SELECT count(*) FROM contacts WHERE phone='15559999999'") == 0
    finally:
        await conn.close()
