"""Catalog embeddings (MVP-048).

Turns an item's text into a 1024-dim vector stored in `catalog_items.embedding` (HNSW index,
migration 012) for semantic kNN. The embedder is **pluggable**: the default is a *deterministic
simulated* embedder (a seeded PRNG per text — no paid API, fully testable), so the whole hybrid
pipeline is exercisable in dev/tests. Turning on `embeddings_provider_enabled` selects the real
hosted provider, which is not wired yet (founder picks one, §9) and fails closed until then.

`embed_pending` is the batch step (a scheduled job iterates orgs and embeds items whose vector
is still NULL). All Meta/provider I/O stays gated.
"""

from __future__ import annotations

import hashlib
import math
import random
from decimal import Decimal
from typing import Any, Protocol

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.config import get_settings

EMBED_DIM = 1024


class Embedder(Protocol):
    async def embed(self, text: str) -> list[float]:
        ...


class SimulatedEmbedder:
    """Deterministic unit vector per text (seeded PRNG). NOT semantic — exercises the pipeline
    mechanics (kNN/RRF/nearest) without a paid API; the real provider gives true similarity."""

    async def embed(self, text: str) -> list[float]:
        seed = int(hashlib.sha256(text.encode()).hexdigest(), 16) % (2**32)
        rng = random.Random(seed)
        vec = [rng.gauss(0.0, 1.0) for _ in range(EMBED_DIM)]
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


class EmbeddingError(Exception):
    """The real embedding provider is unavailable or misconfigured (fail closed)."""


class OpenAiEmbedder:
    """The real embedder (BLOCKER #16): OpenAI `text-embedding-3-small` at `EMBED_DIM` dims (the
    `dimensions` param). Gated — `default_embedder` returns this only when
    `embeddings_provider_enabled`; a missing key or any HTTP/parse failure raises `EmbeddingError`
    (fail closed). The operator holds the key centrally; the token is never logged."""

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings or get_settings()

    async def embed(self, text: str) -> list[float]:
        s = self.settings
        if not s.embeddings_api_key:
            raise EmbeddingError("embeddings enabled but no embeddings_api_key configured")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                resp = await client.post(
                    f"{s.embeddings_api_base}/v1/embeddings",
                    headers={"Authorization": f"Bearer {s.embeddings_api_key}",
                             "content-type": "application/json"},
                    json={"model": s.embeddings_model, "input": text, "dimensions": EMBED_DIM})
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise EmbeddingError(f"OpenAI embeddings request failed: {exc}") from exc
        vec = data["data"][0]["embedding"]
        if len(vec) != EMBED_DIM:
            raise EmbeddingError(f"expected {EMBED_DIM} dims, got {len(vec)}")
        return [float(x) for x in vec]


def default_embedder() -> Embedder:
    return OpenAiEmbedder() if get_settings().embeddings_provider_enabled else SimulatedEmbedder()


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def _item_text(title: str, description: str | None, attributes: dict[str, Any]) -> str:
    parts = [title, description or ""]
    for value in attributes.values():
        if isinstance(value, list):
            parts.extend(str(v) for v in value)
        elif value is not None:
            parts.append(str(value))
    return " ".join(p for p in parts if p)


def _estimate_tokens(text: str) -> int:
    """Rough OpenAI token count (~4 chars/token) — a cost estimate, consistent with how LLM spend
    is estimated in `costs_lite`."""
    return max(1, len(text) // 4)


async def _meter_embedding_cost(session: AsyncSession, tokens: int) -> None:
    """Record per-store embedding spend to `costs_lite` (org from the session's tenant context) so
    it surfaces in the CP-6 cost/margin view. Only called when the real provider is on."""
    s = get_settings()
    org = (
        await session.execute(text("SELECT current_setting('app.org_id', true)"))
    ).scalar()
    if not org:
        return
    cost = Decimal(tokens) / Decimal(1_000_000) * Decimal(str(s.embeddings_price_per_1m_usd))
    await session.execute(
        text("INSERT INTO costs_lite (org_id, node_key, provider, model, tokens_in, cost_usd) "
             "VALUES (:o, 'embeddings', 'openai', :m, :t, :c)"),
        {"o": org, "m": s.embeddings_model, "t": tokens, "c": str(cost)},
    )


async def embed_pending(
    session: AsyncSession, *, embedder: Embedder | None = None, limit: int = 100
) -> int:
    """Embed active items in the current org context whose vector is NULL. Returns the count. When
    the real provider is on, the batch's estimated token spend is metered to `costs_lite`."""
    embedder = embedder or default_embedder()
    rows = (
        await session.execute(
            text(
                "SELECT id, title, description, attributes FROM catalog_items "
                "WHERE embedding IS NULL AND status = 'active' LIMIT :n"
            ),
            {"n": limit},
        )
    ).mappings().all()
    tokens = 0
    for row in rows:
        item_text = _item_text(row["title"], row["description"], row["attributes"] or {})
        tokens += _estimate_tokens(item_text)
        vec = await embedder.embed(item_text)
        await session.execute(
            text("UPDATE catalog_items SET embedding = CAST(:v AS vector) WHERE id = :id"),
            {"v": to_pgvector(vec), "id": str(row["id"])},
        )
    if rows and get_settings().embeddings_provider_enabled:
        await _meter_embedding_cost(session, tokens)
    return len(rows)


async def run_embeddings_batch() -> None:
    """Scheduled job: embed pending items across every org, each in its own tenant context
    (catalog_items is RLS-scoped, so a global pass runs per org via org_scoped_session)."""
    import logging

    from core.common.db import get_sessionmaker
    from core.tenancy.middleware import org_scoped_session

    async with get_sessionmaker()() as s:
        org_ids = (await s.execute(text("SELECT id FROM organizations"))).scalars().all()
    embedder = default_embedder()
    total = 0
    for org_id in org_ids:
        async with org_scoped_session(org_id) as s:
            total += await embed_pending(s, embedder=embedder)
    if total:
        logging.getLogger("core.catalog.embed").info("embedded %d catalog item(s)", total)


def register_jobs() -> None:
    """Register the embeddings batch with the scheduler (called by the scheduler entrypoint)."""
    from core.events.scheduler import register

    register("embeddings_batch", "*/5 * * * *", run_embeddings_batch)
