"""Load + revert (MVP-080) against real Postgres.

Proves confirmed rows become catalog items stamped with `import_batch_id`; a row whose sku already
exists is skipped (identity dedupe); a revert within 30 days archives this batch's UNMUTATED items
and leaves an edited-since item alone (listed for review). Needs a seeded pack. Skips if DB is down.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg
import pytest

from core.catalog import crud
from core.common import db as dbmod
from core.common.config import get_settings
from core.ingestion import extract_csv, load, review, service
from core.tenancy.middleware import org_scoped_session
from tests.conftest import entitle_org


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.import_rows')"))
    finally:
        await conn.close()


@dataclass
class Scene:
    org: uuid.UUID
    pack_id: uuid.UUID


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/import_rows not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Loader')", org)
        # PLAN-5: paid execution follows the plan, so the fixture's store is subscribed.
        await entitle_org(conn, org)
        pack_id = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"p{org.hex[:8]}")
        await conn.execute(
            "INSERT INTO pack_installations (org_id, pack_id, status) VALUES ($1,$2,'active')",
            org, pack_id)
        await conn.execute(
            "INSERT INTO catalog_schemas (pack_id, version, json_schema, identity_keys) "
            "VALUES ($1, 1, $2::jsonb, $3)",
            pack_id, '{"type":"object"}', ["sku"])  # permissive schema, dedupe by sku
    finally:
        await conn.close()
    yield Scene(org, uuid.UUID(str(pack_id)))
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM catalog_items_history WHERE org_id=$1", org)
        await conn.execute("DELETE FROM catalog_idempotency WHERE org_id=$1", org)
        await conn.execute("DELETE FROM import_rows WHERE org_id=$1", org)
        await conn.execute("DELETE FROM import_batches WHERE org_id=$1", org)
        await conn.execute("DELETE FROM tenant_settings WHERE org_id=$1", org)
        await conn.execute("DELETE FROM catalog_items WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM catalog_schemas WHERE pack_id=$1", pack_id)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack_id)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _pipeline(org: uuid.UUID, csv: bytes) -> uuid.UUID:
    """create → extract → validate → confirm-all → return batch id (ready to load)."""
    async with org_scoped_session(org) as s:
        res = await service.create_batch(s, org, source_kind="csv", filename="f.csv", data=csv)
        await s.commit()
    batch_id = uuid.UUID(str(res["batch_id"]))
    async with org_scoped_session(org) as s:
        await extract_csv.extract_batch(s, org, batch_id)
        await s.commit()
    async with org_scoped_session(org) as s:
        await review.validate_batch(s, org, batch_id)
        await review.confirm_all(s, org, batch_id)
        await s.commit()
    return batch_id


async def _count_items(org: uuid.UUID, batch_id: uuid.UUID, status: str) -> int:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM catalog_items WHERE import_batch_id=$1 AND status=$2",
            batch_id, status)
    finally:
        await conn.close()


async def test_load_creates_stamped_items_then_revert_archives(scene: Scene) -> None:
    batch_id = await _pipeline(scene.org, b"Name,SKU,Price\nItem A,S1,100\nItem B,S2,200\n")
    async with org_scoped_session(scene.org) as s:
        result = await load.load_batch(s, scene.org, batch_id)
        await s.commit()
    assert result == {"loaded": 2, "skipped": 0, "failed": 0}
    assert await _count_items(scene.org, batch_id, "active") == 2  # stamped with import_batch_id
    async with org_scoped_session(scene.org) as s:
        b = await service.get_batch(s, scene.org, batch_id)
    assert b is not None and b["state"] == "loaded"

    async with org_scoped_session(scene.org) as s:
        rev = await load.revert_batch(s, scene.org, batch_id)
        await s.commit()
    assert rev == {"reverted": 2, "mutated_skipped": []}
    assert await _count_items(scene.org, batch_id, "active") == 0
    assert await _count_items(scene.org, batch_id, "archived") == 2


async def test_load_skips_a_duplicate_sku(scene: Scene) -> None:
    # a catalog item with sku S1 already exists → the imported row with S1 is skipped
    async with org_scoped_session(scene.org) as s:
        await crud.create_item(
            s, scene.org,
            crud.ItemInput(title="Existing", price_mode="static", attributes={}, sku="S1"),
            actor_id=uuid.uuid4())
        await s.commit()
    batch_id = await _pipeline(scene.org, b"Name,SKU,Price\nItem A,S1,100\nItem B,S2,200\n")
    async with org_scoped_session(scene.org) as s:
        result = await load.load_batch(s, scene.org, batch_id)
        await s.commit()
    assert result["loaded"] == 1 and result["skipped"] == 1  # S1 dup skipped, S2 loaded


async def test_revert_leaves_a_mutated_item_alone(scene: Scene) -> None:
    batch_id = await _pipeline(scene.org, b"Name,SKU,Price\nItem A,S1,100\nItem B,S2,200\n")
    async with org_scoped_session(scene.org) as s:
        await load.load_batch(s, scene.org, batch_id)
        await s.commit()
    # simulate an edit to one loaded item (updated_at > created_at)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "UPDATE catalog_items SET updated_at = now() + interval '1 second' "
            "WHERE import_batch_id=$1 AND sku='S1'", batch_id)
    finally:
        await conn.close()
    async with org_scoped_session(scene.org) as s:
        rev = await load.revert_batch(s, scene.org, batch_id)
        await s.commit()
    assert rev["reverted"] == 1 and len(rev["mutated_skipped"]) == 1  # S1 kept, S2 archived
    assert await _count_items(scene.org, batch_id, "active") == 1
