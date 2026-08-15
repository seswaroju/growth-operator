"""WhatsApp media handling (MVP-037).

Inbound: a media message names a Meta media id + mime. This gates the mime and size, downloads
the bytes (gated MetaClient), **AV-scans fail-closed** (a scanner error quarantines rather than
passes), stores clean bytes in an object store, and returns a descriptor for `messages.media`.
Outbound: a helper uploads bytes to Meta for a send.

The AV scanner and object store are **pluggable** and default to *simulated* implementations,
so the flow is fully testable without the services running. When `media_av_enabled` /
`media_storage_enabled` are set, the real `ClamavScanner` (clamd) and `S3Store` (boto3 →
MinIO/S3) below are used; if the scanner service is unreachable a scan raises `MediaScanError`
and the caller **quarantines** (fail-closed), so a no-op scanner never silently runs. Start the
services with `docker compose --profile media up` (BLOCKERS #12). All Meta I/O stays gated (#3).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from dataclasses import dataclass
from typing import Any, Protocol

from core.channels.whatsapp.meta_client import MetaClient
from core.common.config import get_settings

# Storage lives in `core.media` (DEMO-UX-1). It was never WhatsApp-specific — a generic S3 client
# had simply ended up inside a channel adapter — and the catalog needs the same primitive. A
# catalog importing from `channels.whatsapp` would state something false about the architecture.
#
# Re-exported here so every existing importer of this module keeps working unchanged. WhatsApp
# behaviour is untouched: same classes, same settings, same `default_store()` semantics. The shared
# `MediaStore` additionally offers `get`/`delete`; nothing in this file uses them.
from core.media import MediaStore, S3Store, SimulatedStore, default_store

__all__ = [
    "ClamavScanner", "MediaDescriptor", "MediaRejected", "MediaScanError", "MediaScanner",
    "MediaStore", "S3Store", "SimulatedScanner", "SimulatedStore", "default_scanner",
    "default_store", "ingest_inbound_media", "media_ref",
]


# Tight platform allowlist (pack-extensible later): customer photos, catalog images, PDFs.
ALLOWED_MIME: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "application/pdf"}
)
MAX_MEDIA_BYTES = 16 * 1024 * 1024  # ~Meta's media cap
# WhatsApp inbound message types that carry a media object.
MEDIA_TYPES: frozenset[str] = frozenset({"image", "video", "audio", "document", "sticker"})

# Descriptor statuses.
STORED, REJECTED_MIME, REJECTED_SIZE, QUARANTINED, INFECTED = (
    "stored", "rejected_mime", "rejected_size", "quarantined", "infected",
)


class MediaScanError(Exception):
    """The AV scanner could not complete — the caller must fail closed (quarantine)."""


class MediaScanner(Protocol):
    async def scan(self, data: bytes) -> bool:
        """Return True iff clean. Raise `MediaScanError` if the scan could not run."""
        ...


class SimulatedScanner:
    """Dev/test AV scanner: clean unless configured otherwise. NEVER for production."""

    def __init__(self, *, infected: bool = False, fail: bool = False) -> None:
        self._infected = infected
        self._fail = fail

    async def scan(self, data: bytes) -> bool:
        if self._fail:
            raise MediaScanError("simulated scanner unavailable")
        return not self._infected



class ClamavScanner:
    """Real AV scanner over a clamd (ClamAV daemon) socket. A connection/scan failure raises
    `MediaScanError` so the caller fails closed (quarantine)."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port

    async def scan(self, data: bytes) -> bool:
        def _scan() -> bool:
            import clamd

            result = clamd.ClamdNetworkSocket(self.host, self.port).instream(io.BytesIO(data))
            return result["stream"][0] == "OK"  # ('OK'|'FOUND', signature)

        try:
            return await asyncio.to_thread(_scan)
        except MediaScanError:
            raise
        except Exception as exc:  # noqa: BLE001 - any clamd failure must fail closed
            raise MediaScanError(f"clamav scan failed: {exc}") from exc


def default_scanner() -> MediaScanner:
    s = get_settings()
    if s.media_av_enabled:
        return ClamavScanner(s.clamav_host, s.clamav_port)
    return SimulatedScanner()


@dataclass
class MediaDescriptor:
    media_id: str
    mime: str
    status: str
    storage_ref: str | None = None
    size: int | None = None
    sha256: str | None = None
    reason: str | None = None

    @property
    def quarantined(self) -> bool:
        return self.status == QUARANTINED

    def as_dict(self) -> dict[str, Any]:
        d = {"media_id": self.media_id, "mime": self.mime, "status": self.status}
        for k in ("storage_ref", "size", "sha256", "reason"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        return d


def media_ref(message: dict[str, Any]) -> tuple[str, str] | None:
    """Extract (media_id, mime) from a WhatsApp media message, else None."""
    mtype = message.get("type")
    if mtype in MEDIA_TYPES:
        obj = message.get(mtype) or {}
        media_id = obj.get("id")
        if media_id:
            return str(media_id), str(obj.get("mime_type") or "application/octet-stream")
    return None


async def ingest_inbound_media(
    media_id: str, mime: str, access_token: str, *,
    meta_client: MetaClient | None = None,
    scanner: MediaScanner | None = None,
    store: MediaStore | None = None,
    max_bytes: int = MAX_MEDIA_BYTES,
) -> MediaDescriptor:
    """Gate → download → AV-scan (fail-closed) → store. Never raises for a rejected/dirty item;
    the returned descriptor's ``status`` says what happened (the message still normalizes)."""
    if mime not in ALLOWED_MIME:
        return MediaDescriptor(media_id, mime, REJECTED_MIME, reason=f"mime {mime} not allowed")

    client = meta_client or MetaClient()
    data = await client.download_media(media_id, access_token)
    if len(data) > max_bytes:
        return MediaDescriptor(
            media_id, mime, REJECTED_SIZE, size=len(data),
            reason=f"{len(data)} bytes over {max_bytes} cap",
        )

    scanner = scanner or default_scanner()
    try:
        clean = await scanner.scan(data)
    except MediaScanError as exc:  # fail closed — never store or link unscanned media
        return MediaDescriptor(media_id, mime, QUARANTINED, size=len(data), reason=str(exc))
    if not clean:
        return MediaDescriptor(media_id, mime, INFECTED, size=len(data), reason="failed AV scan")

    digest = hashlib.sha256(data).hexdigest()
    store = store or default_store()
    ref = await store.put(digest, data, mime=mime)
    return MediaDescriptor(media_id, mime, STORED, storage_ref=ref, size=len(data), sha256=digest)


class MediaRejected(Exception):
    """An outbound upload violated the mime/size gates."""


async def upload_outbound_media(
    phone_number_id: str, access_token: str, data: bytes, mime: str, *,
    meta_client: MetaClient | None = None, max_bytes: int = MAX_MEDIA_BYTES,
) -> str:
    """Gate then upload outbound media to Meta (gated). Returns the media id."""
    if mime not in ALLOWED_MIME:
        raise MediaRejected(f"mime {mime} not allowed")
    if len(data) > max_bytes:
        raise MediaRejected(f"{len(data)} bytes over {max_bytes} cap")
    client = meta_client or MetaClient()
    return await client.upload_media(phone_number_id, access_token, data, mime)
