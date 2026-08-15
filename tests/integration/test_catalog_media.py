"""DEMO-UX-1 — catalog image association and tenant isolation, against real Postgres.

The property that matters: knowing an item id, or an object key, is not access. Both are guessable
or leakable in ways authorization is not, so every read resolves the item under the tenant boundary
before a single byte is fetched.
"""

from __future__ import annotations

import io
import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from PIL import Image

from core.catalog import media as catalog_media
from core.common import db as dbmod
from core.common.config import get_settings
from core.media.images import ImageRejected
from core.tenancy.middleware import org_scoped_session
from core.tenancy.repository import set_org_context


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.catalog_items')"))
    finally:
        await conn.close()


def _photo(size: tuple[int, int] = (1200, 900)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (190, 150, 70)).save(buffer, "JPEG")
    return buffer.getvalue()


class TwoStores:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self.conn = conn
        self.a, self.b = uuid.uuid4(), uuid.uuid4()
        self.created_pack: uuid.UUID | None = None

    async def setup(self) -> None:
        for oid, name in ((self.a, "Store A"), (self.b, "Store B")):
            await self.conn.execute(
                "INSERT INTO organizations (id, name, vertical) VALUES ($1,$2,'jewelry')",
                oid, f"{name}-{oid.hex[:6]}")
        pack = await self.conn.fetchval("SELECT id FROM packs WHERE slug='jewelry'")
        if pack is None:
            pack = uuid.uuid4()
            await self.conn.execute(
                "INSERT INTO packs (id, slug, version, platform_api, manifest, bundle_uri, "
                "signature, status) VALUES ($1,'jewelry','1','1','{}'::jsonb,'x','x','published')",
                pack)
            self.created_pack = pack
        self.item_a = await self._item(self.a, pack, "A-RING-1")
        self.item_b = await self._item(self.b, pack, "B-RING-1")

    async def _item(self, org: uuid.UUID, pack: uuid.UUID, sku: str) -> uuid.UUID:
        return await self.conn.fetchval(
            "INSERT INTO catalog_items (org_id, pack_id, sku, title, price_mode, "
            " attributes_schema_ver) VALUES ($1,$2,$3,$4,'static',1) RETURNING id",
            org, pack, sku, f"Item {sku}")


@pytest.fixture()
async def stores() -> AsyncIterator[TwoStores]:
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    conn = await asyncpg.connect(_dsn())
    two = TwoStores(conn)
    try:
        await two.setup()
        yield two
    finally:
        for oid in (two.a, two.b):
            await conn.execute("DELETE FROM catalog_items WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        if two.created_pack is not None:
            await conn.execute("DELETE FROM packs WHERE id=$1", two.created_pack)
        await conn.close()
        await dbmod.get_engine().dispose()
        dbmod.get_engine.cache_clear()
        dbmod.get_sessionmaker.cache_clear()


# ---- the happy path ------------------------------------------------------------------------


async def test_upload_stores_derivatives_and_associates_them(stores: TwoStores) -> None:
    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        stored = await catalog_media.attach(
            s, stores.a, stores.item_a, data=_photo((2400, 1600)), declared_mime="image/jpeg")
        await s.commit()
    assert (stored.width, stored.height) == (1600, 1067)

    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        primary = await catalog_media.read(s, stores.a, stores.item_a, variant="primary")
        thumb = await catalog_media.read(s, stores.a, stores.item_a, variant="thumbnail")
    assert primary and thumb
    assert len(thumb[0]) < len(primary[0])
    assert primary[1] == "image/jpeg"


async def test_an_item_without_an_image_reads_as_absent(stores: TwoStores) -> None:
    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        assert await catalog_media.read(s, stores.a, stores.item_a, variant="primary") is None


# ---- tenant isolation ------------------------------------------------------------------------


async def test_another_tenant_cannot_read_the_image(stores: TwoStores) -> None:
    """The headline property. Store B knows the item id and asks for it directly."""
    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        await catalog_media.attach(
            s, stores.a, stores.item_a, data=_photo(), declared_mime="image/jpeg")
        await s.commit()

    async with org_scoped_session(stores.b) as s:
        await set_org_context(s, stores.b)
        with pytest.raises(catalog_media.ItemNotFound):
            await catalog_media.read(s, stores.b, stores.item_a, variant="primary")


async def test_another_tenant_cannot_attach_to_someone_elses_item(stores: TwoStores) -> None:
    async with org_scoped_session(stores.b) as s:
        await set_org_context(s, stores.b)
        with pytest.raises(catalog_media.ItemNotFound):
            await catalog_media.attach(
                s, stores.b, stores.item_a, data=_photo(), declared_mime="image/jpeg")


async def test_another_tenant_cannot_remove_someone_elses_image(stores: TwoStores) -> None:
    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        await catalog_media.attach(
            s, stores.a, stores.item_a, data=_photo(), declared_mime="image/jpeg")
        await s.commit()

    async with org_scoped_session(stores.b) as s:
        await set_org_context(s, stores.b)
        with pytest.raises(catalog_media.ItemNotFound):
            await catalog_media.remove(s, stores.b, stores.item_a)

    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        assert await catalog_media.read(s, stores.a, stores.item_a, variant="primary")


async def test_absent_and_foreign_items_are_indistinguishable(stores: TwoStores) -> None:
    """Both raise the same exception. Distinguishing them would let a caller enumerate another
    store's catalog by watching which ids answer differently."""
    async with org_scoped_session(stores.b) as s:
        await set_org_context(s, stores.b)
        with pytest.raises(catalog_media.ItemNotFound):
            await catalog_media.read(s, stores.b, stores.item_a, variant="primary")
        with pytest.raises(catalog_media.ItemNotFound):
            await catalog_media.read(s, stores.b, uuid.uuid4(), variant="primary")


async def test_object_keys_are_namespaced_by_org(stores: TwoStores) -> None:
    """Authorization is the control, but a key layout that cannot accidentally cross tenants is
    worth having underneath it."""
    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        stored = await catalog_media.attach(
            s, stores.a, stores.item_a, data=_photo(), declared_mime="image/jpeg")
        await s.commit()
    for key in (stored.primary_key, stored.thumbnail_key, stored.original_key):
        assert key.startswith(f"{stores.a}/")


# ---- lifecycle -------------------------------------------------------------------------------


async def test_replacing_an_image_removes_the_previous_objects(stores: TwoStores) -> None:
    """No silent orphan accumulation — the failure this whole ticket exists to stop repeating."""
    from core.media import default_store

    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        first = await catalog_media.attach(
            s, stores.a, stores.item_a, data=_photo((1000, 800)), declared_mime="image/jpeg")
        await s.commit()
    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        second = await catalog_media.attach(
            s, stores.a, stores.item_a, data=_photo((1200, 400)), declared_mime="image/jpeg")
        await s.commit()

    assert second.primary_key != first.primary_key
    store = default_store()
    assert await store.get(first.primary_key) is None, "the replaced object was left behind"
    assert await store.get(second.primary_key) is not None


async def test_remove_clears_association(stores: TwoStores) -> None:
    from core.media import default_store

    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        stored = await catalog_media.attach(
            s, stores.a, stores.item_a, data=_photo(), declared_mime="image/jpeg")
        await s.commit()
    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        assert await catalog_media.remove(s, stores.a, stores.item_a) is True
        await s.commit()

    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        assert await catalog_media.read(s, stores.a, stores.item_a, variant="primary") is None
    assert await default_store().get(stored.primary_key) is None
    # jsonb comes back as a JSON string from asyncpg, so compare the parsed value.
    media = await stores.conn.fetchval(
        "SELECT media FROM catalog_items WHERE id=$1", stores.item_a)
    assert json.loads(media) == []


async def test_removing_when_there_is_no_image_is_not_an_error(stores: TwoStores) -> None:
    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        assert await catalog_media.remove(s, stores.a, stores.item_a) is False


async def test_the_original_is_retained_but_not_addressable(stores: TwoStores) -> None:
    """Kept so a future derivative can be regenerated without asking the merchant to upload again;
    never served, because it carries whatever metadata the camera wrote."""
    from core.media import default_store

    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        stored = await catalog_media.attach(
            s, stores.a, stores.item_a, data=_photo(), declared_mime="image/jpeg")
        await s.commit()
    assert await default_store().get(stored.original_key) is not None
    assert "original" not in catalog_media.VARIANTS

    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        assert await catalog_media.read(s, stores.a, stores.item_a, variant="original") is None


async def test_a_rejected_upload_leaves_the_existing_image_intact(stores: TwoStores) -> None:
    """A merchant who picks the wrong file must not lose the photograph they already had."""
    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        good = await catalog_media.attach(
            s, stores.a, stores.item_a, data=_photo(), declared_mime="image/jpeg")
        await s.commit()

    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        with pytest.raises(ImageRejected):
            await catalog_media.attach(
                s, stores.a, stores.item_a, data=b"not an image at all",
                declared_mime="image/jpeg")

    async with org_scoped_session(stores.a) as s:
        await set_org_context(s, stores.a)
        still = await catalog_media.read(s, stores.a, stores.item_a, variant="primary")
    assert still is not None
    keys = await stores.conn.fetchval(
        "SELECT media_keys FROM catalog_items WHERE id=$1", stores.item_a)
    assert json.loads(keys)["primary"] == good.primary_key
