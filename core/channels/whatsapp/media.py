"""WhatsApp media handling (MVP-037).

Inbound: a media message names a Meta media id + mime. This gates the mime and size, downloads
the bytes (gated MetaClient), **AV-scans fail-closed** (a scanner error quarantines rather than
passes), stores clean bytes in an object store, and returns a descriptor for `messages.media`.
Outbound: a helper uploads bytes to Meta for a send.

The AV scanner and object store are **pluggable** and default to *simulated* implementations
so the flow is fully testable with no new dependencies (§9). The real clamav + MinIO/S3
adapters are not wired yet; enabling them via config before they exist fails closed
(`media_av_enabled` / `media_storage_enabled` → NotImplementedError) so a no-op simulated AV
scanner can never silently run in production (BLOCKERS #12). All Meta I/O stays gated (#3).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from core.channels.whatsapp.meta_client import MetaClient
from core.common.config import get_settings

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


class MediaStore(Protocol):
    async def put(self, key: str, data: bytes, *, mime: str) -> str:
        """Persist bytes and return a storage reference."""
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


class SimulatedStore:
    """Dev/test object store — keeps bytes in-process and returns a sim:// ref."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, *, mime: str) -> str:
        self.objects[key] = data
        return f"sim://media/{key}"


_SIM_STORE = SimulatedStore()  # shared so a stored ref is retrievable within a process


def default_scanner() -> MediaScanner:
    if get_settings().media_av_enabled:  # real clamav adapter not built yet (§9, BLOCKERS #12)
        raise NotImplementedError("media_av_enabled is set but no real AV scanner is wired")
    return SimulatedScanner()


def default_store() -> MediaStore:
    if get_settings().media_storage_enabled:  # real MinIO/S3 adapter not built yet
        raise NotImplementedError("media_storage_enabled is set but no real store is wired")
    return _SIM_STORE


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
