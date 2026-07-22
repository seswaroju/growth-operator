"""Readiness endpoint integration tests (MVP-007).

Skips without a reachable, migrated DB. Verifies /readyz is 200 only when pg + redis
are up and the schema is at head, and 503 when the migration is behind head.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest

from core.api import health
from core.common import db as dbmod
from core.common.config import get_settings


def _dsn() -> str:
    return get_settings().database_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(
            await conn.fetchval("SELECT to_regclass('public.alembic_version') IS NOT NULL")
        )
    finally:
        await conn.close()


@pytest.fixture()
async def api() -> AsyncIterator[httpx.AsyncClient]:
    if not await _db_ready():
        pytest.skip(
            "Postgres not reachable or migration not applied — run "
            "`docker compose -f infra/docker/docker-compose.dev.yml up -d postgres redis` "
            "and `uv run alembic upgrade head`."
        )
    from core.api.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_readyz_ok_when_healthy(api: httpx.AsyncClient) -> None:
    r = await api.get("/readyz")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"postgres": True, "redis": True, "migration_head": True}


async def test_readyz_503_when_migration_behind_head(
    api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate the DB being behind code without mutating the shared schema.
    monkeypatch.setattr(health, "_head_revision", lambda: "not-a-real-head")
    r = await api.get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["migration_head"] is False
