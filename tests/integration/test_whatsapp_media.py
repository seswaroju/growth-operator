"""WhatsApp media handling (MVP-037).

Unit-tests the gates (mime allowlist, size cap), fail-closed AV scanning (scanner error →
quarantine; infected → rejected), storage of clean media, and the outbound upload helper —
all with simulated adapters, no new deps. Then drives the normalizer end-to-end: an image
message is downloaded/scanned/stored and linked on `messages.media`; a disallowed mime still
normalizes (text fallback); a scanner outage quarantines and emits `alert.ops.v1`. DB tests
skip when Postgres is unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.channels.whatsapp import media, normalizer
from core.channels.whatsapp.credentials import store_credentials
from core.channels.whatsapp.media import (
    INFECTED,
    QUARANTINED,
    REJECTED_MIME,
    REJECTED_SIZE,
    STORED,
    MediaRejected,
    SimulatedScanner,
    SimulatedStore,
)
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.middleware import org_scoped_session


class CountingMeta:
    def __init__(self, data: bytes = b"clean-bytes") -> None:
        self.data = data
        self.downloads = 0
        self.uploads = 0

    @property
    def simulated(self) -> bool:
        return True

    async def download_media(self, media_id: str, access_token: str) -> bytes:
        self.downloads += 1
        return self.data

    async def upload_media(self, pnid: str, token: str, data: bytes, mime: str) -> str:
        self.uploads += 1
        return "media.UP"


# ---- Unit: gates + scan + store -------------------------------------------------------


async def test_disallowed_mime_rejected_without_download() -> None:
    meta = CountingMeta()
    d = await media.ingest_inbound_media("m1", "image/gif", "tok", meta_client=meta)  # type: ignore[arg-type]
    assert d.status == REJECTED_MIME and meta.downloads == 0  # never fetched


async def test_oversize_rejected_at_cap() -> None:
    meta = CountingMeta(data=b"x" * 100)
    d = await media.ingest_inbound_media(
        "m2", "image/jpeg", "tok", meta_client=meta, max_bytes=10,  # type: ignore[arg-type]
    )
    assert d.status == REJECTED_SIZE and d.size == 100


async def test_scanner_error_quarantines_fail_closed() -> None:
    d = await media.ingest_inbound_media(
        "m3", "image/jpeg", "tok",
        meta_client=CountingMeta(), scanner=SimulatedScanner(fail=True),  # type: ignore[arg-type]
        store=SimulatedStore(),
    )
    assert d.status == QUARANTINED and d.storage_ref is None  # never stored unscanned


async def test_infected_rejected() -> None:
    d = await media.ingest_inbound_media(
        "m4", "image/jpeg", "tok",
        meta_client=CountingMeta(), scanner=SimulatedScanner(infected=True),  # type: ignore[arg-type]
        store=SimulatedStore(),
    )
    assert d.status == INFECTED and d.storage_ref is None


async def test_clean_media_stored() -> None:
    store = SimulatedStore()
    d = await media.ingest_inbound_media(
        "m5", "image/png", "tok",
        meta_client=CountingMeta(data=b"png-bytes"), scanner=SimulatedScanner(),  # type: ignore[arg-type]
        store=store,
    )
    assert d.status == STORED and d.storage_ref and d.sha256 and d.size == len(b"png-bytes")
    assert store.objects[d.sha256] == b"png-bytes"  # actually persisted


def test_media_ref_extraction() -> None:
    img = {"type": "image", "image": {"id": "MID", "mime_type": "image/jpeg"}}
    assert media.media_ref(img) == ("MID", "image/jpeg")
    assert media.media_ref({"type": "text", "text": {"body": "hi"}}) is None


async def test_outbound_upload_gates_and_uploads() -> None:
    meta = CountingMeta()
    with pytest.raises(MediaRejected):
        await media.upload_outbound_media("pn", "tok", b"x", "image/gif", meta_client=meta)  # type: ignore[arg-type]
    with pytest.raises(MediaRejected):
        await media.upload_outbound_media(
            "pn", "tok", b"x" * 100, "image/jpeg", meta_client=meta, max_bytes=10,  # type: ignore[arg-type]
        )
    media_id = await media.upload_outbound_media(
        "pn", "tok", b"ok", "image/jpeg", meta_client=meta,  # type: ignore[arg-type]
    )
    assert media_id == "media.UP" and meta.uploads == 1


def test_enabling_flags_selects_real_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    # Flags off → simulated; flags on → the real clamav/S3 adapters (no service call here).
    assert isinstance(media.default_scanner(), media.SimulatedScanner)
    monkeypatch.setenv("GROWTH_OPERATOR_MEDIA_AV_ENABLED", "true")
    assert isinstance(media.default_scanner(), media.ClamavScanner)
    monkeypatch.setenv("GROWTH_OPERATOR_MEDIA_STORAGE_ENABLED", "true")
    assert isinstance(media.default_store(), media.S3Store)


# ---- E2E: normalizer media path -------------------------------------------------------


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


def _media_webhook(pnid: str, wamid: str, phone: str, mtype: str, mime: str) -> str:
    return json.dumps({"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": pnid},
        "messages": [{"id": wamid, "from": phone, "type": mtype,
                      mtype: {"id": f"media-{wamid}", "mime_type": mime}}],
    }}]}]})


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
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'M')", org)
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
            credentials={"waba_id": "w", "phone_number_id": pnid, "access_token": "tok"},
        )
    yield {"org": org, "pnid": pnid}
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM event_outbox WHERE org_id=$1", org)
        await conn.execute("DELETE FROM webhook_events WHERE provider='whatsapp'")
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _insert(pnid: str, wamid: str, phone: str, mtype: str, mime: str) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO webhook_events (provider, external_id, payload) "
            "VALUES ('whatsapp', $1, $2::jsonb)",
            wamid, _media_webhook(pnid, wamid, phone, mtype, mime),
        )
    finally:
        await conn.close()


async def _message_media(wamid: str) -> list[dict]:
    conn = await asyncpg.connect(_dsn())
    try:
        raw = await conn.fetchval("SELECT media FROM messages WHERE provider_message_id=$1", wamid)
    finally:
        await conn.close()
    return json.loads(raw) if isinstance(raw, str) else (raw or [])


async def test_image_message_stored_and_linked(scene: dict) -> None:
    org, pnid = scene["org"], scene["pnid"]
    wamid = f"wamid.{uuid.uuid4().hex}"
    await _insert(pnid, wamid, "15551110000", "image", "image/jpeg")
    assert await normalizer.normalize_pending() >= 1

    linked = await _message_media(wamid)
    assert len(linked) == 1 and linked[0]["status"] == STORED and linked[0]["storage_ref"]
    conn = await asyncpg.connect(_dsn())
    try:
        # body is the placeholder; the media descriptor rides on msg.received.v1.
        row = await conn.fetchrow("SELECT body FROM messages WHERE provider_message_id=$1", wamid)
        assert row["body"] == "[image]"
        emitted = await conn.fetchval(
            "SELECT payload->'media' FROM event_outbox "
            "WHERE org_id=$1 AND type='msg.received.v1' ORDER BY created_at DESC LIMIT 1", org
        )
    finally:
        await conn.close()
    assert len(json.loads(emitted) if isinstance(emitted, str) else emitted) == 1


async def test_disallowed_mime_still_normalizes(scene: dict) -> None:
    pnid = scene["pnid"]
    wamid = f"wamid.{uuid.uuid4().hex}"
    await _insert(pnid, wamid, "15552220000", "image", "image/gif")  # gif not allowed
    await normalizer.normalize_pending()

    linked = await _message_media(wamid)
    assert linked and linked[0]["status"] == REJECTED_MIME
    conn = await asyncpg.connect(_dsn())
    try:  # the message itself is still there (text fallback), not dropped
        assert await conn.fetchval(
            "SELECT body FROM messages WHERE provider_message_id=$1", wamid
        ) == "[image]"
    finally:
        await conn.close()


async def test_scanner_down_quarantines_and_alerts(
    scene: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    org, pnid = scene["org"], scene["pnid"]
    monkeypatch.setattr(media, "default_scanner", lambda: SimulatedScanner(fail=True))
    wamid = f"wamid.{uuid.uuid4().hex}"
    await _insert(pnid, wamid, "15553330000", "image", "image/png")
    await normalizer.normalize_pending()

    linked = await _message_media(wamid)
    assert linked and linked[0]["status"] == QUARANTINED
    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval(
            "SELECT count(*) FROM event_outbox WHERE org_id=$1 AND type='alert.ops.v1'", org
        ) == 1
    finally:
        await conn.close()
