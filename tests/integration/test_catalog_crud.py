"""Catalog item CRUD + history (MVP-045) against real Postgres under app_rw.

Covers create/get, identity-key dedup (409 with existing id), Idempotency-Key replay, keyset
cursor pagination, If-Match update, soft-delete, and that every mutation writes a history row
with the actor. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.catalog import crud
from core.catalog.crud import (
    DuplicateIdentity,
    ItemInput,
    NoPackInstalled,
    PreconditionFailed,
    etag,
)
from core.catalog.validate import ValidationProblems
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.middleware import org_scoped_session


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


def _item(huid: str, sku: str | None = None, title: str = "Gold chain") -> ItemInput:
    return ItemInput(title=title, price_mode="computed", attributes={"huid": huid}, sku=sku)


@pytest.fixture()
async def scene() -> AsyncIterator[dict]:
    if not await _db_ready():
        pytest.skip("Postgres/catalog (012) not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    slug = f"jw{org.hex[:8]}"
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'C')", org)
        pack_id = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'2','>=1',$2::jsonb,'u','s','published') RETURNING id",
            slug, "{}",
        )
        await conn.execute(
            "INSERT INTO pack_installations (org_id, pack_id, status) VALUES ($1,$2,'active')",
            org, pack_id,
        )
        await conn.execute(
            "INSERT INTO catalog_schemas (pack_id, version, json_schema, identity_keys) "
            "VALUES ($1, 2, $2::jsonb, $3)",
            pack_id,
            '{"type":"object","properties":{"huid":{"type":"string"}}}',  # allows test attrs
            ["huid", "sku"],
        )
    finally:
        await conn.close()
    yield {"org": org, "slug": slug, "pack_id": pack_id}
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM catalog_items_history WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)  # cascades items/idem
        await conn.execute("DELETE FROM catalog_schemas WHERE pack_id=$1", pack_id)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack_id)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _history_ops(org: uuid.UUID, item_id: uuid.UUID) -> list[tuple[str, uuid.UUID | None]]:
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            "SELECT operation, changed_by FROM catalog_items_history WHERE id=$1 "
            "ORDER BY changed_at",
            item_id,
        )
        return [(r["operation"], r["changed_by"]) for r in rows]
    finally:
        await conn.close()


async def test_create_get_and_history(scene: dict) -> None:
    org, actor = scene["org"], uuid.uuid4()
    async with org_scoped_session(org) as s:
        item_id, created = await crud.create_item(s, org, _item("HUID01", "SKU1"), actor_id=actor)
        assert created is True
        got = await crud.get_item(s, org, item_id)
    assert got is not None
    assert got["title"] == "Gold chain" and got["attributes"]["huid"] == "HUID01"
    assert got["attributes_schema_ver"] == 2 and got["status"] == "active"
    assert await _history_ops(org, item_id) == [("insert", actor)]


async def test_identity_duplicate_raises_with_existing_id(scene: dict) -> None:
    org = scene["org"]
    async with org_scoped_session(org) as s:
        first, _ = await crud.create_item(s, org, _item("HUID-DUP"), actor_id=uuid.uuid4())
    async with org_scoped_session(org) as s:
        with pytest.raises(DuplicateIdentity) as ei:
            await crud.create_item(s, org, _item("HUID-DUP", title="Other"), actor_id=uuid.uuid4())
    assert ei.value.existing_id == first


async def test_idempotency_key_replays_same_item(scene: dict) -> None:
    org = scene["org"]
    async with org_scoped_session(org) as s:
        first, c1 = await crud.create_item(
            s, org, _item("HUID-A", "SKUA"), actor_id=uuid.uuid4(), idempotency_key="key-1"
        )
    async with org_scoped_session(org) as s:
        second, c2 = await crud.create_item(
            s, org, _item("HUID-B", "SKUB"), actor_id=uuid.uuid4(), idempotency_key="key-1"
        )
    assert c1 is True and c2 is False and second == first
    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval("SELECT count(*) FROM catalog_items WHERE org_id=$1", org) == 1
    finally:
        await conn.close()


async def test_cursor_pagination_walks_all(scene: dict) -> None:
    org = scene["org"]
    async with org_scoped_session(org) as s:
        for i in range(5):
            await crud.create_item(s, org, _item(f"H{i}", f"S{i}"), actor_id=uuid.uuid4())

    seen: list[uuid.UUID] = []
    cursor = None
    async with org_scoped_session(org) as s:
        while True:
            items, cursor = await crud.list_items(s, org, cursor=cursor, limit=2)
            seen.extend(i["id"] for i in items)
            if cursor is None:
                break
    assert len(seen) == 5 and len(set(seen)) == 5  # every item, once


async def test_update_if_match_and_history(scene: dict) -> None:
    org, actor = scene["org"], uuid.uuid4()
    async with org_scoped_session(org) as s:
        item_id, _ = await crud.create_item(s, org, _item("HUID-U"), actor_id=actor)
        got = await crud.get_item(s, org, item_id)
        tag = etag(got["updated_at"])  # type: ignore[index]

    async with org_scoped_session(org) as s:
        with pytest.raises(PreconditionFailed):
            await crud.update_item(
                s, org, item_id, {"title": "x"}, actor_id=actor, reason="e", if_match='"stale"'
            )
    async with org_scoped_session(org) as s:
        updated = await crud.update_item(
            s, org, item_id, {"title": "Updated"}, actor_id=actor, reason="fix", if_match=tag
        )
    assert updated["title"] == "Updated"
    assert ("update", actor) in await _history_ops(org, item_id)


async def test_soft_delete_archives_and_delists(scene: dict) -> None:
    org, actor = scene["org"], uuid.uuid4()
    async with org_scoped_session(org) as s:
        item_id, _ = await crud.create_item(s, org, _item("HUID-D"), actor_id=actor)
    async with org_scoped_session(org) as s:
        await crud.delete_item(s, org, item_id, actor_id=actor, reason="discontinued")
        got = await crud.get_item(s, org, item_id)
        listed, _ = await crud.list_items(s, org)
    assert got is not None and got["status"] == "archived"
    assert item_id not in [i["id"] for i in listed]  # archived items are delisted
    assert ("delete", actor) in await _history_ops(org, item_id)


async def test_create_rejects_invalid_attributes(scene: dict) -> None:
    # The fixture schema allows only 'huid'; an unknown attribute fails validation (MVP-046).
    org = scene["org"]
    bad = ItemInput(title="x", price_mode="static", attributes={"huid": "H", "bogus": 1})
    async with org_scoped_session(org) as s:
        with pytest.raises(ValidationProblems) as ei:
            await crud.create_item(s, org, bad, actor_id=uuid.uuid4())
    assert ei.value.problems and ei.value.problems[0].rule == "schema"


async def test_create_without_pack_raises() -> None:
    if not await _db_ready():
        pytest.skip("db not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'N')", org)
    finally:
        await conn.close()
    try:
        async with org_scoped_session(org) as s:
            with pytest.raises(NoPackInstalled):
                await crud.create_item(s, org, _item("H"), actor_id=uuid.uuid4())
    finally:
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        finally:
            await conn.close()
        await dbmod.get_engine().dispose()
        dbmod.get_engine.cache_clear()
        dbmod.get_sessionmaker.cache_clear()
