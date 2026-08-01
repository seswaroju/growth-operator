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
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.config import get_settings

EMBED_DIM = 1024


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]:
        ...


class SimulatedEmbedder:
    """Deterministic unit vector per text (seeded PRNG). NOT semantic — exercises the pipeline
    mechanics (kNN/RRF/nearest) without a paid API; the real provider gives true similarity."""

    def embed(self, text: str) -> list[float]:
        seed = int(hashlib.sha256(text.encode()).hexdigest(), 16) % (2**32)
        rng = random.Random(seed)
        vec = [rng.gauss(0.0, 1.0) for _ in range(EMBED_DIM)]
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


def default_embedder() -> Embedder:
    if get_settings().embeddings_provider_enabled:  # real provider not chosen/wired yet (§9)
        raise NotImplementedError(
            "embeddings_provider_enabled is set but no hosted embedding provider is wired"
        )
    return SimulatedEmbedder()


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


async def embed_pending(
    session: AsyncSession, *, embedder: Embedder | None = None, limit: int = 100
) -> int:
    """Embed active items in the current org context whose vector is NULL. Returns the count."""
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
    for row in rows:
        vec = embedder.embed(_item_text(row["title"], row["description"], row["attributes"] or {}))
        await session.execute(
            text("UPDATE catalog_items SET embedding = CAST(:v AS vector) WHERE id = :id"),
            {"v": to_pgvector(vec), "id": str(row["id"])},
        )
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
