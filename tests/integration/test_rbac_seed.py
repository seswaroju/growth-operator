"""Migration-003 RBAC seed ↔ constants drift test (MVP-015).

Asserts the role→permission grants seeded by migration 003 exactly match
core/tenancy/permissions.ROLE_PERMISSIONS, so the DB catalog and the enforcement constants
can never silently diverge. Skips when no migrated database is reachable.
"""

from __future__ import annotations

import asyncpg
import pytest

from core.common.config import get_settings
from core.tenancy import permissions


def _dsn() -> str:
    return get_settings().database_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        reg = await conn.fetchval("SELECT to_regclass('public.role_permissions')")
        return reg is not None
    finally:
        await conn.close()


async def test_seed_matches_constants() -> None:
    if not await _db_ready():
        pytest.skip("Postgres not reachable or migration 003 not applied")
    conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            """
            SELECT r.name AS role, p.name AS perm
            FROM role_permissions rp
            JOIN roles r ON r.id = rp.role_id
            JOIN permissions p ON p.id = rp.permission_id
            """
        )
    finally:
        await conn.close()

    seeded: dict[str, set[str]] = {}
    for row in rows:
        seeded.setdefault(row["role"], set()).add(row["perm"])

    expected = {role: set(perms) for role, perms in permissions.ROLE_PERMISSIONS.items()}
    assert seeded == expected


async def test_all_permissions_present_in_catalog() -> None:
    if not await _db_ready():
        pytest.skip("Postgres not reachable or migration 003 not applied")
    conn = await asyncpg.connect(_dsn())
    try:
        names = {r["name"] for r in await conn.fetch("SELECT name FROM permissions")}
    finally:
        await conn.close()
    assert names == set(permissions.ALL_PERMISSIONS)
