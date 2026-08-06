"""Least-privilege lock for the cross-tenant operator flag (support-tickets track, security #1).

The `app.platform_admin='on'` GUC is the platform's only cross-tenant read escape hatch. Its blast
radius MUST be exactly one table — `support_tickets`. These tests freeze that:

- **Structural (exhaustive):** across *every* RLS policy in the database, the flag may appear in
  `support_tickets`' policies and nowhere else. The moment a future migration adds the exception to
  another table, this fails — so the operator can never silently gain cross-tenant reach into
  customers' conversations, contacts, catalog, revenue, etc.
- **Runtime (behavioural):** with the flag on and no org context, a representative org-scoped table
  (`contacts`) still returns zero rows — the flag is inert outside `support_tickets`.

Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common.config import get_settings

# The complete set of tables allowed to honour the cross-tenant operator flag. Adding a table here
# is a deliberate security decision that must come with its own isolation tests.
ALLOWED_CROSS_TENANT_TABLES = {"support_tickets"}


def _owner_dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


def _app_rw_dsn() -> str:
    return get_settings().database_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_owner_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.support_tickets')"))
    finally:
        await conn.close()


async def test_platform_admin_flag_referenced_by_exactly_the_allowlisted_tables() -> None:
    """Exhaustive structural lock: no table outside the allowlist may reference app.platform_admin
    in any RLS policy (USING or WITH CHECK)."""
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    conn = await asyncpg.connect(_owner_dsn())
    try:
        rows = await conn.fetch(
            """
            SELECT c.relname AS tbl,
                   COALESCE(pg_get_expr(p.polqual, p.polrelid), '') AS using_expr,
                   COALESCE(pg_get_expr(p.polwithcheck, p.polrelid), '') AS check_expr
            FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
            """
        )
    finally:
        await conn.close()
    referencing = {
        r["tbl"] for r in rows if "platform_admin" in (r["using_expr"] + r["check_expr"])
    }
    assert referencing == ALLOWED_CROSS_TENANT_TABLES, (
        "the app.platform_admin cross-tenant exception must appear ONLY on "
        f"{sorted(ALLOWED_CROSS_TENANT_TABLES)}; found on {sorted(referencing)}. "
        "A new table honouring the operator flag widens the cross-tenant blast radius — add it to "
        "ALLOWED_CROSS_TENANT_TABLES only with its own isolation tests."
    )


async def test_platform_admin_flag_only_ever_appears_in_using_never_in_insert_check() -> None:
    """Defence in depth: even on support_tickets, the flag must never appear in an INSERT WITH CHECK
    — reads/updates may cross tenants, writes (INSERT) never can."""
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    conn = await asyncpg.connect(_owner_dsn())
    try:
        insert_checks = await conn.fetch(
            """
            SELECT c.relname AS tbl, COALESCE(pg_get_expr(p.polwithcheck, p.polrelid), '') AS chk
            FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
            WHERE p.polcmd = 'a'  -- 'a' = INSERT
            """
        )
    finally:
        await conn.close()
    leaky = [r["tbl"] for r in insert_checks if "platform_admin" in r["chk"]]
    assert leaky == [], f"INSERT policy must never honour the admin flag; leaked on {leaky}"


@pytest.fixture()
async def two_orgs_with_contacts() -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    a, b = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_owner_dsn())
    try:
        for org in (a, b):
            await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'pa-scope')", org)
            await conn.execute(
                "INSERT INTO contacts (org_id, phone, consent_status) "
                "VALUES ($1,$2,'granted')", org, f"+91{org.int % 10**10:010d}")
    finally:
        await conn.close()
    yield a, b
    conn = await asyncpg.connect(_owner_dsn())
    try:
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", [a, b])
    finally:
        await conn.close()


async def test_admin_flag_is_inert_on_other_tenant_tables(
    two_orgs_with_contacts: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Runtime proof: with app.platform_admin='on' and no org context, an operator sees ZERO rows in
    a non-allowlisted org-scoped table (`contacts`) — the flag grants nothing there."""
    conn = await asyncpg.connect(_app_rw_dsn())
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.platform_admin', 'on', true)")
            n = await conn.fetchval("SELECT count(*) FROM contacts")
        assert n == 0, f"admin flag leaked {n} cross-tenant contacts — it must be inert here"
    finally:
        await conn.close()


async def test_platform_access_log_is_append_only() -> None:
    """The admin-plane audit trail must be tamper-evident: the app role may INSERT but NEVER UPDATE
    or DELETE (enforced by a trigger that fires for all roles, so a re-granted privilege can't
    bypass it). This is what makes the operator's cross-tenant activity record trustworthy."""
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    actor = uuid.uuid4()
    conn = await asyncpg.connect(_app_rw_dsn())
    try:
        await conn.execute(
            "INSERT INTO platform_access_log (actor_user_id, action) VALUES ($1,'test.probe')",
            actor)
        row_id = await conn.fetchval(
            "SELECT id FROM platform_access_log WHERE actor_user_id=$1", actor)
        assert row_id is not None  # INSERT (append) is allowed

        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await conn.execute(
                "UPDATE platform_access_log SET action='tamper' WHERE id=$1", row_id)
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await conn.execute("DELETE FROM platform_access_log WHERE id=$1", row_id)
    finally:
        await conn.close()
        owner = await asyncpg.connect(_owner_dsn())
        try:
            await owner.execute(
                "ALTER TABLE platform_access_log DISABLE TRIGGER trg_platform_access_log_immutable")
            await owner.execute("DELETE FROM platform_access_log WHERE actor_user_id=$1", actor)
            await owner.execute(
                "ALTER TABLE platform_access_log ENABLE TRIGGER trg_platform_access_log_immutable")
        finally:
            await owner.close()
