"""Catalog text search (MVP-047) against real Postgres under app_rw.

Seeds items whose search_text is built from title + description + projected attributes, then
checks websearch matching (incl. exact tokens like '22k'), recall via a projected alias
attribute, and that editing a title refreshes search_text. Skips when the DB is unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.catalog import crud, search
from core.catalog.crud import ItemInput
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.middleware import org_scoped_session

_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "category": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}},
    },
})


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


@pytest.fixture()
async def scene() -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres/catalog (012) not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'S')", org)
        pack_id = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"sr{org.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO pack_installations (org_id, pack_id, status) VALUES ($1,$2,'active')",
            org, pack_id,
        )
        await conn.execute(
            "INSERT INTO catalog_schemas (pack_id, version, json_schema, search_projection, "
            "identity_keys) VALUES ($1, 1, $2::jsonb, $3, $4)",
            pack_id, _SCHEMA, ["category", "aliases"], ["sku"],
        )
    finally:
        await conn.close()
    yield org
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM catalog_items_history WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM catalog_schemas WHERE pack_id=$1", pack_id)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack_id)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _seed(org: uuid.UUID) -> dict[str, uuid.UUID]:
    ids: dict[str, uuid.UUID] = {}
    async with org_scoped_session(org) as s:
        for key, title, sku, attrs in [
            ("chain", "Gold chain", "A", {"category": "chain"}),
            ("22k", "22k gold chain", "B", {"category": "chain"}),
            ("atta", "Atta flour", "C", {"category": "grocery", "aliases": ["aata", "wheat"]}),
        ]:
            item_id, _ = await crud.create_item(
                s, org, ItemInput(title=title, price_mode="static", attributes=attrs, sku=sku),
                actor_id=uuid.uuid4(),
            )
            ids[key] = item_id
    return ids


async def test_query_matches_and_exact_token(scene: uuid.UUID) -> None:
    ids = await _seed(scene)
    async with org_scoped_session(scene) as s:
        chain = {r["id"] for r in await search.search_items(s, scene, "chain")}
        exact = [r["id"] for r in await search.search_items(s, scene, "22k chain")]
    assert ids["chain"] in chain and ids["22k"] in chain
    assert exact == [ids["22k"]]  # only the item carrying both '22k' and 'chain'


async def test_projected_alias_recall(scene: uuid.UUID) -> None:
    ids = await _seed(scene)
    async with org_scoped_session(scene) as s:
        hits = {r["id"] for r in await search.search_items(s, scene, "aata")}
    assert ids["atta"] in hits  # matched via the projected 'aliases' attribute


async def test_search_text_refreshes_on_title_edit(scene: uuid.UUID) -> None:
    ids = await _seed(scene)
    async with org_scoped_session(scene) as s:
        before = {r["id"] for r in await search.search_items(s, scene, "platinum")}
        assert ids["chain"] not in before
        await crud.update_item(
            s, scene, ids["chain"], {"title": "Platinum bangle"},
            actor_id=uuid.uuid4(), reason="retitle",
        )
        after = {r["id"] for r in await search.search_items(s, scene, "platinum")}
    assert ids["chain"] in after  # the new title term is now searchable
