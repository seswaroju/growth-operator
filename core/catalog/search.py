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

# search_text = simple(text) || english(text); a query is matched the same way.
_TSVECTOR = "to_tsvector('simple', :t) || to_tsvector('english', :t)"
_TSQUERY = "(websearch_to_tsquery('simple', :q) || websearch_to_tsquery('english', :q))"

_ITEM_FIELDS = (
    "id, sku, title, description, media, price_mode, base_price_minor, currency, "
    "availability, attributes, attributes_schema_ver, status"
)


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


async def search_items(
    session: AsyncSession, org_id: UUID, query: str, *, k: int = 8
) -> list[dict[str, Any]]:
    """Rank active items against a websearch query (BM25-ish). RLS scopes to the caller's org."""
    rows = (
        await session.execute(
            text(
                f"SELECT {_ITEM_FIELDS}, ts_rank(search_text, {_TSQUERY}) AS rank "
                f"FROM catalog_items WHERE status = 'active' AND search_text @@ {_TSQUERY} "
                "ORDER BY rank DESC, id LIMIT :k"
            ),
            {"q": query, "k": k},
        )
    ).mappings().all()
    return [dict(r) for r in rows]
