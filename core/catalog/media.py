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

**Archiving an item retains its image and its association.** Archive is soft deletion — a record of
something that happened, kept so history stays readable — and destroying the photograph would make
that history worse without freeing anything a pilot cares about. An archived item that is restored
comes back complete rather than blank, and a merchant who archives an item by mistake has lost
nothing. Bytes are removed only by an explicit image removal or a replacement, both of which are a
deliberate act on that image. Nothing in this module deletes on archive, and nothing should be added
that does without an actual retention requirement saying so.
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

    def as_media(self, item_id: UUID) -> list[str]:
        """What goes into `catalog_items.media` — and therefore into every API response.

        A **logical application path**, never a storage key. The first version stored
        `vaylorn-media://{org_id}/catalog/{uuid}.jpg`, which put the bucket layout and the org
        namespace into `CatalogItemOut` and straight into the browser. Storage keys are private
        infrastructure metadata: publishing them tells an attacker exactly what to ask an
        object store for, and pins the API response to a storage backend we might change.

        The real keys live in `media_keys`, which no response model reads.

        Still `list[str]` so multi-image support arrives without a schema change.
        """
        return [f"/v1/catalog/items/{item_id}/image"]


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
    """Validate, derive, store, associate, **and commit**. Raises `ItemNotFound`/`ImageRejected`.

    Committing here rather than in the router is deliberate, and it is the only way the ordering
    invariant can hold. Postgres and object storage cannot participate in one transaction, so the
    order of operations *is* the correctness argument:

        write NEW objects → stage association → COMMIT → only then delete OLD objects

    The first version deleted the old objects before the router committed. If that commit then
    failed, the rollback restored an association pointing at bytes that no longer existed — the
    merchant's product photograph would simply be gone, with the database insisting it was there.
    Deleting only after a successful commit means a failure anywhere leaves the OLD association and
    the OLD bytes both intact, which is the state the merchant expects.

    On commit failure the NEW objects are removed instead: they are unreferenced by definition, and
    leaving them would accumulate exactly the orphans this module is careful about.

    No attempt is made to fake atomicity across the two systems. The guarantee is narrower and
    honest: **the database never references bytes that have been deleted.**
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

    # Every key that reaches storage is recorded as it is written, not assumed from the plan. The
    # earlier version only cleaned up after a failed commit, so a thumbnail PUT that failed after
    # the primary succeeded — or a failing UPDATE — left the written objects behind forever. The
    # invariant is simpler than a list of cases: **anything written before a successful commit is
    # removed if that commit does not happen.**
    written: list[str] = []
    try:
        for key, payload, mime in (
            (primary_key, derived.primary, derived.mime),
            (thumbnail_key, derived.thumbnail, derived.mime),
            (original_key, data, declared_mime or derived.mime),
        ):
            await store.put(key, payload, mime=mime)
            written.append(key)

        stored = StoredImage(
            primary_key=primary_key, thumbnail_key=thumbnail_key, original_key=original_key,
            width=derived.width, height=derived.height, mime=derived.mime)

        await session.execute(
            text("UPDATE catalog_items SET media = CAST(:m AS jsonb), "
                 "media_keys = CAST(:k AS jsonb) WHERE id = :i AND org_id = :o"),
            {"m": json.dumps(stored.as_media(item_id)), "k": json.dumps({
                "primary": primary_key, "thumbnail": thumbnail_key, "original": original_key,
                "width": derived.width, "height": derived.height, "mime": derived.mime}),
             "i": str(item_id), "o": str(org_id)})

        await session.commit()
    except Exception:
        # Covers a partial PUT, a failing UPDATE and a failing COMMIT alike. The association never
        # became durable, so everything written here is unreferenced: remove it and leave the
        # previous image exactly as the merchant left it.
        await session.rollback()
        await _discard_keys(written, reason="attach_failed")
        raise

    # Only now. The association is durable, so nothing can serve the old objects any more, and a
    # storage hiccup here costs disk rather than correctness.
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
    """Clear the association, **commit**, then delete the objects. True when there was an image.

    Same ordering rule as `attach`, for the same reason: if the bytes went first and the commit then
    failed, the row would still claim an image that no longer exists. Deleting after the commit
    means a failure leaves the image intact and the merchant simply tries again.
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
    try:
        await session.commit()
    except Exception:
        # Nothing was deleted yet, so the image is still whole and still referenced.
        await session.rollback()
        raise
    await _discard(keys, reason="removed")
    return True


async def _discard_keys(keys: list[str], *, reason: str) -> None:
    """Delete a list of raw keys, best effort. Never raises — see `_discard`."""
    store = default_store()
    for key in keys:
        try:
            await store.delete(key)
        except Exception:  # noqa: BLE001 - cleanup must not mask the original failure
            logger.warning("catalog.media.orphan: key=%s reason=%s", key, reason)


async def _discard(keys: dict[str, Any], *, reason: str) -> None:
    """Delete stored objects, best effort.

    Never raises. The caller has already made the durable change; failing the request now would
    report an error for an operation that actually succeeded. Orphans are logged so they can be
    swept later rather than accumulating invisibly — which is the failure this whole ticket exists
    to stop repeating.
    """
    await _discard_keys(
        [str(keys[name]) for name in ("primary", "thumbnail", "original") if keys.get(name)],
        reason=reason)
