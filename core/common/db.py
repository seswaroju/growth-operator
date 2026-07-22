"""Async SQLAlchemy engine + session factory.

First introduced by MVP-011 (identity persistence). Kept deliberately small: a single
lazily-constructed engine bound to `Settings.database_url`, and a FastAPI-friendly
`get_session` dependency that yields one `AsyncSession` per request/transaction.

Tenant context (`SET LOCAL app.org_id`) is intentionally NOT applied here — the identity
tables are global (no RLS). Org-scoped context arrives with the tenant middleware in
MVP-016.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.common.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Process-wide async engine (created on first use)."""
    settings = get_settings()
    return create_async_engine(settings.database_url, pool_pre_ping=True, future=True)


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield a session and commit/rollback around the request."""
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
