"""Shared test bootstrap.

Ensures the non-superuser `app_rw` role exists before any test runs, so the app engine
(which now connects as `app_rw` — MVP-016) can log in and RLS is actually enforced. Runs
`infra/db/roles.sql` via the migrator (owner) connection; a no-op when the DB is
unreachable (those tests skip themselves).
"""

from __future__ import annotations

import asyncio
import pathlib

import asyncpg
import pytest

from core.common.config import get_settings

_ROLES_SQL = pathlib.Path(__file__).resolve().parents[1] / "infra" / "db" / "roles.sql"


@pytest.fixture(scope="session", autouse=True)
def ensure_app_rw_role() -> None:
    async def _run() -> None:
        dsn = get_settings().database_migrator_url.replace("+asyncpg", "")
        try:
            conn = await asyncpg.connect(dsn, timeout=3)
        except Exception:
            return  # DB not up — DB-backed tests skip; nothing to bootstrap
        try:
            await conn.execute(_ROLES_SQL.read_text())
        finally:
            await conn.close()

    asyncio.run(_run())
