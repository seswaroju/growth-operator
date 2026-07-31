"""Opt-out keyword net (MVP-036): STOP auto-suppress + transactional confirmation.

Unit-checks the keyword matcher (en / romanised hi / te), then drives the full inbound path:
a STOP message auto-suppresses the contact for marketing and, on the first suppression only,
sends the fixed transactional confirmation through the gated send adapter (msg.sent.v1). A
second STOP does not re-confirm. DB tests skip when Postgres is unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.channels.whatsapp import normalizer
from core.channels.whatsapp.credentials import store_credentials
from core.channels.whatsapp.keywords import is_stop_keyword
from core.channels.whatsapp.normalizer import STOP_CONFIRM_TEXT
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.middleware import org_scoped_session


@pytest.mark.parametrize(
    "body",
    ["STOP", "stop", "  Stop! ", "unsubscribe", "Unsub", "band karo", "Band Karo.", "ఆపండి"],
)
def test_keyword_matches(body: str) -> None:
    assert is_stop_keyword(body) is True


@pytest.mark.parametrize(
    "body",
    ["I couldn't stop thinking about the ring", "stopwatch", "please send more", "", "start"],
)
def test_keyword_does_not_match(body: str) -> None:
    assert is_stop_keyword(body) is False


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


def _webhook(pnid: str, wamid: str, phone: str, body: str) -> str:
    return json.dumps(
        {"entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": pnid},
            "messages": [{"id": wamid, "from": phone, "type": "text", "text": {"body": body}}],
        }}]}]}
    )


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
async def scene() -> AsyncIterator[dict]:
    if not await _db_ready():
        pytest.skip("Postgres/messaging+channel_credentials not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    pnid = f"pn-{org.hex[:8]}"
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'W')", org)
        channel_id = await conn.fetchval(
            "INSERT INTO channels (org_id, type, external_id, credentials_ref) "
            "VALUES ($1,'whatsapp',$2,'channel_credentials') RETURNING id",
            org, pnid,
        )
    finally:
        await conn.close()
    async with org_scoped_session(org) as s:
        await store_credentials(
            s, org_id=org, channel_id=channel_id,
            credentials={"waba_id": "w1", "phone_number_id": pnid, "access_token": "tok"},
        )
    yield {"org": org, "pnid": pnid}
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM event_outbox WHERE org_id=$1", org)
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


async def test_stop_suppresses_and_confirms_once(scene: dict) -> None:
    org, pnid = scene["org"], scene["pnid"]
    phone = "15557654321"
    await _insert_webhook(pnid, f"wamid.{uuid.uuid4().hex}", phone, "STOP")
    assert await normalizer.normalize_pending() >= 1

    conn = await asyncpg.connect(_dsn())
    try:
        contact_id = await conn.fetchval(
            "SELECT id FROM contacts WHERE org_id=$1 AND phone=$2", org, phone
        )
        # Contact is suppressed for marketing.
        assert await conn.fetchval(
            "SELECT count(*) FROM suppressions "
            "WHERE org_id=$1 AND contact_id=$2 AND scope='marketing'",
            org, contact_id,
        ) == 1
        # A transactional confirmation went out (through the gated send adapter → status sent).
        confirm = await conn.fetchrow(
            "SELECT status FROM messages "
            "WHERE org_id=$1 AND direction='outbound' AND body=$2",
            org, STOP_CONFIRM_TEXT,
        )
        assert confirm is not None and confirm["status"] == "sent"
        assert await conn.fetchval(
            "SELECT count(*) FROM event_outbox WHERE org_id=$1 AND type='msg.sent.v1'", org
        ) == 1
    finally:
        await conn.close()

    # A second STOP must not send a second confirmation (already suppressed).
    await _insert_webhook(pnid, f"wamid.{uuid.uuid4().hex}", phone, "stop please")
    await normalizer.normalize_pending()
    conn = await asyncpg.connect(_dsn())
    try:
        # "stop please" is not a whole-message keyword → still exactly one confirm from the first.
        confirms = await conn.fetchval(
            "SELECT count(*) FROM messages "
            "WHERE org_id=$1 AND direction='outbound' AND body=$2",
            org, STOP_CONFIRM_TEXT,
        )
        assert confirms == 1
    finally:
        await conn.close()


async def test_repeated_exact_stop_does_not_double_confirm(scene: dict) -> None:
    org, pnid = scene["org"], scene["pnid"]
    phone = "15550001111"
    for _ in range(2):
        await _insert_webhook(pnid, f"wamid.{uuid.uuid4().hex}", phone, "STOP")
        await normalizer.normalize_pending()

    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval(
            "SELECT count(*) FROM messages "
            "WHERE org_id=$1 AND direction='outbound' AND body=$2",
            org, STOP_CONFIRM_TEXT,
        ) == 1  # suppression is idempotent → confirm only on first
    finally:
        await conn.close()
