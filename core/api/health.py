"""Liveness + readiness endpoints (MVP-007).

- GET /healthz — liveness. Always 200 while the process is up; deliberately does NOT
  touch Postgres/Redis, so a dependency outage never gets the container killed.
- GET /readyz — readiness. 200 only when Postgres and Redis are reachable AND the DB
  schema is at Alembic head; otherwise 503. This is what load balancers / compose gate
  traffic on.

Deep external checks (e.g. Meta API reachability) are out of scope — MVP-031 channel health.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Response, status
from redis.asyncio import Redis
from sqlalchemy import text

from core.common.config import get_settings
from core.common.db import get_engine

router = APIRouter(tags=["health"])

_PROBE_TIMEOUT = 1.0  # seconds — a ready check must be fast or it's not ready
_REPO_ROOT = Path(__file__).resolve().parents[2]


@router.get("/healthz", summary="Liveness — process is up (no dependency checks)")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@lru_cache(maxsize=1)
def _head_revision() -> str | None:
    """Alembic head for this codebase (static per process — safe to cache)."""
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


async def _pg_ok() -> bool:
    try:
        async with get_engine().connect() as conn:
            await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=_PROBE_TIMEOUT)
        return True
    except Exception:
        return False


async def _redis_ok() -> bool:
    client = Redis.from_url(get_settings().redis_url)
    try:
        return bool(await asyncio.wait_for(client.ping(), timeout=_PROBE_TIMEOUT))
    except Exception:
        return False
    finally:
        await client.aclose()


async def _migration_current() -> bool:
    """True iff the DB's applied revision matches the codebase head."""
    try:
        async with get_engine().connect() as conn:
            result = await asyncio.wait_for(
                conn.execute(text("SELECT version_num FROM alembic_version")),
                timeout=_PROBE_TIMEOUT,
            )
            current = result.scalar_one_or_none()
        return current is not None and current == _head_revision()
    except Exception:
        return False


@router.get("/readyz", summary="Readiness — pg + redis reachable and schema at head")
async def readyz(response: Response) -> dict[str, object]:
    pg, redis_ok, migration = await asyncio.gather(
        _pg_ok(), _redis_ok(), _migration_current()
    )
    checks = {"postgres": pg, "redis": redis_ok, "migration_head": migration}
    ready = all(checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not_ready", "checks": checks}
