"""Catalog embeddings + hybrid RRF search (MVP-048), gated-simulated.

RRF fusion is unit-tested; against real Postgres the simulated embedder fills `embedding`, and
`hybrid_search` fuses BM25 + kNN — a text match returns results, and a no-match query returns
empty results with 3 `nearest` neighbours (the empty→nearest contract). The simulated embedder
is deterministic (no paid API); it exercises the pipeline mechanics, not semantic quality.
Skips when the DB is unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.catalog import crud, embed, search
from core.catalog.crud import ItemInput
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.middleware import org_scoped_session

_SCHEMA = json.dumps({"type": "object", "properties": {"category": {"type": "string"}}})


def test_rrf_fuse_is_deterministic_and_rewards_agreement() -> None:
    # 'b' and 'c' appear in both lists → they outrank items in only one list.
    fused = search.rrf_fuse([["a", "b", "c"], ["b", "c", "d"]])
    assert fused[:2] == ["b", "c"]
    assert search.rrf_fuse([["a", "b", "c"], ["b", "c", "d"]]) == fused  # stable


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
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'E')", org)
        pack_id = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"em{org.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO pack_installations (org_id, pack_id, status) VALUES ($1,$2,'active')",
            org, pack_id,
        )
        await conn.execute(
            "INSERT INTO catalog_schemas (pack_id, version, json_schema, search_projection, "
            "identity_keys) VALUES ($1, 1, $2::jsonb, $3, $4)",
            pack_id, _SCHEMA, ["category"], ["sku"],
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


async def _seed(org: uuid.UUID) -> None:
    async with org_scoped_session(org) as s:
        for title, sku in [("Gold chain", "A"), ("Silver ring", "B"), ("Diamond pendant", "C")]:
            await crud.create_item(
                s, org, ItemInput(title=title, price_mode="static",
                                  attributes={"category": "jewel"}, sku=sku),
                actor_id=uuid.uuid4(),
            )


async def test_embed_pending_fills_vectors(scene: uuid.UUID) -> None:
    await _seed(scene)
    async with org_scoped_session(scene) as s:
        assert await embed.embed_pending(s) == 3  # three items embedded
        again = await embed.embed_pending(s)
    assert again == 0  # idempotent — nothing left NULL
    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval(
            "SELECT count(*) FROM catalog_items WHERE org_id=$1 AND embedding IS NOT NULL", scene
        ) == 3
    finally:
        await conn.close()


async def test_embed_pending_meters_cost_when_provider_on(
    scene: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(scene)
    monkeypatch.setenv("GROWTH_OPERATOR_EMBEDDINGS_PROVIDER_ENABLED", "true")
    # Inject the deterministic embedder so there's no real API call — the metering path still fires
    # because the provider flag is on (BLOCKER #16: per-store embedding spend → costs_lite).
    async with org_scoped_session(scene) as s:
        assert await embed.embed_pending(s, embedder=embed.SimulatedEmbedder()) == 3
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT node_key, provider, model, tokens_in, cost_usd FROM costs_lite "
            "WHERE org_id=$1 AND node_key='embeddings'", scene)
    finally:
        await conn.close()
    assert row is not None  # this store's embedding spend is now in the ledger (CP-6 view)
    assert row["provider"] == "openai" and row["model"] == "text-embedding-3-small"
    assert row["tokens_in"] > 0 and row["cost_usd"] >= 0


async def test_no_cost_metered_when_provider_off(scene: uuid.UUID) -> None:
    await _seed(scene)
    async with org_scoped_session(scene) as s:
        await embed.embed_pending(s)  # simulated (default off) → free, nothing metered
    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval(
            "SELECT count(*) FROM costs_lite WHERE org_id=$1 AND node_key='embeddings'", scene) == 0
    finally:
        await conn.close()


async def test_hybrid_returns_bm25_matches(scene: uuid.UUID) -> None:
    await _seed(scene)
    async with org_scoped_session(scene) as s:
        await embed.embed_pending(s)
        results, nearest = await search.hybrid_search(s, scene, "gold chain")
    titles = [r["title"] for r in results]
    assert "Gold chain" in titles and nearest == []  # confident results → no nearest fallback


async def test_hybrid_empty_results_carry_three_nearest(scene: uuid.UUID) -> None:
    await _seed(scene)
    async with org_scoped_session(scene) as s:
        await embed.embed_pending(s)
        results, nearest = await search.hybrid_search(s, scene, "zzqqxx-nomatch-token")
    assert results == []  # no keyword match + no semantically-close item
    assert len(nearest) == 3  # the empty→nearest contract (gr-01)


async def test_hybrid_is_deterministic(scene: uuid.UUID) -> None:
    await _seed(scene)
    async with org_scoped_session(scene) as s:
        await embed.embed_pending(s)
        first, _ = await search.hybrid_search(s, scene, "gold")
        second, _ = await search.hybrid_search(s, scene, "gold")
    assert [r["id"] for r in first] == [r["id"] for r in second]
