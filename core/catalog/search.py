"""Catalog text search (MVP-047).

`search_text` is a tsvector built from an item's title + description + the pack's `x-search`
projected attributes, indexed with GIN (migration 012). It is maintained on every write and
combines the `simple` (exact tokens like "22k", vernacular aliases) and `english` (stemmed)
configurations, so a `websearch_to_tsquery` matches both. Ranking is `ts_rank` (BM25-ish).
Hybrid semantic fusion (embeddings + RRF) lands in MVP-048.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.catalog.embed import Embedder, default_embedder, to_pgvector

# search_text = simple(text) || english(text); a query is matched the same way.
_TSVECTOR = "to_tsvector('simple', :t) || to_tsvector('english', :t)"
_TSQUERY = "(websearch_to_tsquery('simple', :q) || websearch_to_tsquery('english', :q))"

_ITEM_FIELDS = (
    "id, sku, title, description, media, price_mode, base_price_minor, currency, "
    "availability, attributes, attributes_schema_ver, status"
)

# A kNN neighbour joins `results` only if this close (cosine distance); farther ones are
# offered as `nearest` when there are no confident results (the empty→nearest contract).
SEMANTIC_MAX_DISTANCE = 0.35
RRF_K = 60


def build_text(
    title: str, description: str | None, attributes: dict[str, Any], projection: list[str]
) -> str:
    """Compose the searchable text from title, description, and projected attribute values."""
    parts: list[str] = [title, description or ""]
    for field in projection:
        value = attributes.get(field)
        if isinstance(value, list):
            parts.extend(str(v) for v in value)
        elif value is not None:
            parts.append(str(value))
    return " ".join(p for p in parts if p)


async def refresh(session: AsyncSession, item_id: UUID, projection: list[str]) -> None:
    """Rebuild an item's `search_text` from its current values (called after every write)."""
    row = (
        await session.execute(
            text("SELECT title, description, attributes FROM catalog_items WHERE id = :id"),
            {"id": str(item_id)},
        )
    ).mappings().first()
    if row is None:
        return
    txt = build_text(row["title"], row["description"], row["attributes"] or {}, projection)
    await session.execute(
        text(f"UPDATE catalog_items SET search_text = {_TSVECTOR} WHERE id = :id"),
        {"t": txt, "id": str(item_id)},
    )


def _filter_sql(filters: dict[str, str] | None) -> tuple[str, dict[str, Any]]:
    """Build an attribute-equality WHERE fragment pushed into both search branches."""
    if not filters:
        return "", {}
    clauses, params = [], {}
    for i, (key, value) in enumerate(filters.items()):
        clauses.append(f"AND attributes->>:fk{i} = :fv{i}")
        params[f"fk{i}"], params[f"fv{i}"] = key, value
    return " " + " ".join(clauses), params


async def search_items(
    session: AsyncSession, org_id: UUID, query: str, *, k: int = 8,
    filters: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Rank active items against a websearch query (BM25-ish). RLS scopes to the caller's org."""
    fsql, fparams = _filter_sql(filters)
    rows = (
        await session.execute(
            text(
                f"SELECT {_ITEM_FIELDS}, ts_rank(search_text, {_TSQUERY}) AS rank "
                f"FROM catalog_items WHERE status = 'active' AND search_text @@ {_TSQUERY}{fsql} "
                "ORDER BY rank DESC, id LIMIT :k"
            ),
            {"q": query, "k": k, **fparams},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def rrf_fuse(ranked_lists: list[list[Any]], *, k: int = RRF_K) -> list[Any]:
    """Reciprocal Rank Fusion: score = Σ 1/(k + rank). Deterministic (score desc, then the
    order an id was first seen for ties)."""
    scores: dict[Any, float] = {}
    first_seen: dict[Any, int] = {}
    for lst in ranked_lists:
        for rank, item_id in enumerate(lst):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
            first_seen.setdefault(item_id, len(first_seen))
    return sorted(scores, key=lambda i: (-scores[i], first_seen[i]))


async def _knn(
    session: AsyncSession, query_vector: str, k: int, *,
    filter_sql: str = "", filter_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                f"SELECT {_ITEM_FIELDS}, (embedding <=> CAST(:qv AS vector)) AS distance "
                f"FROM catalog_items WHERE status = 'active' AND embedding IS NOT NULL{filter_sql} "
                "ORDER BY embedding <=> CAST(:qv AS vector) LIMIT :k"
            ),
            {"qv": query_vector, "k": k, **(filter_params or {})},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def hybrid_search(
    session: AsyncSession, org_id: UUID, query: str, *, k: int = 8,
    filters: dict[str, str] | None = None, embedder: Embedder | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fuse BM25 keyword hits with semantic kNN (RRF). Returns (results, nearest): when there
    are no confident results, `nearest` carries the 3 closest neighbours (empty→nearest)."""
    embedder = embedder or default_embedder()
    fsql, fparams = _filter_sql(filters)
    bm25 = await search_items(session, org_id, query, k=k * 2, filters=filters)
    knn = await _knn(
        session, to_pgvector(embedder.embed(query)), k * 2,
        filter_sql=fsql, filter_params=fparams,
    )
    bm25_ids = [r["id"] for r in bm25]
    knn_confident = [r["id"] for r in knn if r["distance"] <= SEMANTIC_MAX_DISTANCE]
    fused = rrf_fuse([bm25_ids, knn_confident])[:k]
    by_id = {r["id"]: r for r in knn} | {r["id"]: r for r in bm25}
    results = [by_id[i] for i in fused]
    nearest = list(knn[:3]) if not results else []
    return results, nearest
