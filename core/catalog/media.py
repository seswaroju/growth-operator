"""Catalog product images — server-owned association and lifecycle (DEMO-UX-1).

A merchant uploads one primary photograph per catalog item. Everything about *where* it is stored
is decided here; the client sends bytes and nothing else.

**The client cannot name a reference.** `catalog_items.media` is a JSON array, so a request could
previously put `s3://other-tenant/…`, `http://attacker/…`, or another store's object key straight
into it. Association is now server-side only: the browser uploads a file, the server generates the
key, and the API's item-write path no longer accepts media at all. A client-supplied storage
reference is an SSRF and cross-tenant read primitive in one field.

**Every read re-authorizes.** Serving is an authenticated, tenant-scoped endpoint: the item is
resolved under RLS first, and only then are the bytes fetched. Knowing an item id or an object key
is not access. Nothing here mints a presigned URL — a bearer token in a query string outlives the
session and lands in logs, history and referrer headers.

**Only derivatives are served.** The uploaded original is retained privately (it is the merchant's
photograph and re-deriving from it later beats asking them to upload again) but has no merchant-
facing route. What customers and the browser see is the normalised, EXIF-stripped derivative.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.media import default_store, object_key
from core.media.images import Derivatives, process

logger = logging.getLogger(__name__)

#: Which derivative a request wants. `original` is deliberately absent — it is retained for
#: re-derivation, not for serving.
VARIANTS = ("primary", "thumbnail")


class ItemNotFound(Exception):
    """The item does not exist **for this tenant**. Deliberately one exception for "absent" and
    "belongs to someone else": distinguishing them would let a caller enumerate another store's
    catalog by watching which ids answer differently."""


@dataclass(frozen=True)
class StoredImage:
    primary_key: str
    thumbnail_key: str
    original_key: str
    width: int
    height: int
    mime: str

    def as_media(self) -> list[str]:
        """What goes into `catalog_items.media`.

        Kept as `list[str]` so multi-image support can arrive without a schema change, and stored
        as an internal `vaylorn-media://` reference rather than a bucket path: the storage backend
        is an implementation detail, and a row that hardcodes `s3://` is a row that has to be
        rewritten the day storage moves.
        """
        return [f"vaylorn-media://{self.primary_key}"]


async def _item_exists(session: AsyncSession, org_id: UUID, item_id: UUID) -> bool:
    """Resolve under the tenant boundary. RLS is already applied by the caller's session context;
    the explicit `org_id` predicate is belt and braces, not the only check."""
    found = (await session.execute(
        text("SELECT 1 FROM catalog_items WHERE id = :i AND org_id = :o"),
        {"i": str(item_id), "o": str(org_id)})).first()
    return found is not None


async def _current_keys(session: AsyncSession, org_id: UUID, item_id: UUID) -> dict[str, str]:
    row = (await session.execute(
        text("SELECT media_keys FROM catalog_items WHERE id = :i AND org_id = :o"),
        {"i": str(item_id), "o": str(org_id)})).scalar_one_or_none()
    if not row:
        return {}
    return dict(json.loads(row)) if isinstance(row, str) else dict(row)


async def attach(
    session: AsyncSession, org_id: UUID, item_id: UUID, *, data: bytes, declared_mime: str | None,
) -> StoredImage:
    """Validate, derive, store, and associate. Raises `ItemNotFound` or `ImageRejected`.

    Order matters. New objects are written **before** the association is updated and the old ones
    are removed only **after** it commits, so a failure anywhere leaves the item pointing at bytes
    that exist. The opposite order can leave a merchant looking at a broken image, which is worse
    than briefly holding one orphan.
    """
    if not await _item_exists(session, org_id, item_id):
        raise ItemNotFound

    # Decode and resize off the event loop: this is real CPU work and blocking here would stall
    # every other request on the process for the duration of an upload.
    derived: Derivatives = await asyncio.to_thread(process, data, declared_mime=declared_mime)

    store = default_store()
    previous = await _current_keys(session, org_id, item_id)

    # Server-generated, org-namespaced keys. A client never supplies one.
    primary_key = object_key(org_id, "catalog", suffix=".jpg")
    thumbnail_key = object_key(org_id, "catalog", suffix=".jpg")
    original_key = object_key(org_id, "catalog-original")

    await store.put(primary_key, derived.primary, mime=derived.mime)
    await store.put(thumbnail_key, derived.thumbnail, mime=derived.mime)
    await store.put(original_key, data, mime=declared_mime or derived.mime)

    stored = StoredImage(
        primary_key=primary_key, thumbnail_key=thumbnail_key, original_key=original_key,
        width=derived.width, height=derived.height, mime=derived.mime)

    await session.execute(
        text("UPDATE catalog_items SET media = CAST(:m AS jsonb), "
             "media_keys = CAST(:k AS jsonb) WHERE id = :i AND org_id = :o"),
        {"m": json.dumps(stored.as_media()), "k": json.dumps({
            "primary": primary_key, "thumbnail": thumbnail_key, "original": original_key,
            "width": derived.width, "height": derived.height, "mime": derived.mime}),
         "i": str(item_id), "o": str(org_id)})

    # Old objects last, and never fatally: the association is already correct, so a storage hiccup
    # here costs disk, not correctness. Logged so orphans are visible rather than silent.
    await _discard(previous, reason="replaced")
    return stored


async def read(
    session: AsyncSession, org_id: UUID, item_id: UUID, *, variant: str
) -> tuple[bytes, str] | None:
    """Bytes for one derivative, or None when the item has no image.

    Re-authorizes: the item is resolved for this tenant before any object is fetched, so knowing an
    id or a key gets a caller nothing. `original` is not addressable.
    """
    if variant not in VARIANTS:
        return None
    if not await _item_exists(session, org_id, item_id):
        raise ItemNotFound

    keys = await _current_keys(session, org_id, item_id)
    key = keys.get(variant)
    if not key:
        return None
    data = await default_store().get(str(key))
    if data is None:
        # The row points at an object that is gone — worth knowing about, and better than serving
        # a confusing 500.
        logger.warning("catalog.media.missing_object: item=%s variant=%s", item_id, variant)
        return None
    return data, str(keys.get("mime") or "image/jpeg")


async def remove(session: AsyncSession, org_id: UUID, item_id: UUID) -> bool:
    """Clear the association and delete the objects. True when there was an image to remove.

    Association first: once the row no longer references the objects, nothing can serve them, and
    a failure deleting bytes leaves an orphan rather than a broken item.
    """
    if not await _item_exists(session, org_id, item_id):
        raise ItemNotFound
    keys = await _current_keys(session, org_id, item_id)
    if not keys:
        return False
    await session.execute(
        text("UPDATE catalog_items SET media = '[]'::jsonb, media_keys = NULL "
             "WHERE id = :i AND org_id = :o"),
        {"i": str(item_id), "o": str(org_id)})
    await _discard(keys, reason="removed")
    return True


async def _discard(keys: dict[str, Any], *, reason: str) -> None:
    """Delete stored objects, best effort.

    Never raises. The caller has already made the durable change; failing the request now would
    report an error for an operation that actually succeeded. Orphans are logged so they can be
    swept later rather than accumulating invisibly — which is the failure this whole ticket exists
    to stop repeating.
    """
    store = default_store()
    for name in ("primary", "thumbnail", "original"):
        key = keys.get(name)
        if not key:
            continue
        try:
            await store.delete(str(key))
        except Exception:  # noqa: BLE001 - cleanup must not fail a completed operation
            logger.warning("catalog.media.orphan: %s key=%s reason=%s", name, key, reason)
