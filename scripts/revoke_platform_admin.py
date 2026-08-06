"""Revoke a user's platform-admin (operator) access — governance for the cross-tenant plane.

Usage:  uv run python scripts/revoke_platform_admin.py <email>
   or:  make revoke-admin EMAIL=you@example.com

Removes the user from the `platform_admins` allowlist — immediately (their next request loses the
operator queue and gets 403). The revocation is recorded in the append-only `platform_access_log`.
"""

from __future__ import annotations

import asyncio
import json
import sys

import asyncpg

from core.common.config import get_settings


async def revoke(email: str) -> int:
    dsn = get_settings().database_migrator_url.replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    try:
        user_id = await conn.fetchval("SELECT id FROM users WHERE email = $1", email)
        if user_id is None:
            print(f"No user with email {email!r}.")
            return 1
        result = await conn.execute("DELETE FROM platform_admins WHERE user_id = $1", user_id)
        await conn.execute(
            "INSERT INTO platform_access_log (actor_user_id, action, detail) "
            "VALUES ($1, 'platform.admin.revoked', $2::jsonb)",
            user_id, json.dumps({"email": email, "via": "script"}),
        )
        removed = result.split()[-1] if result else "0"
        print(f"OK — revoked platform admin from {email} ({user_id}). Rows removed: {removed}.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: uv run python scripts/revoke_platform_admin.py <email>")
        raise SystemExit(2)
    raise SystemExit(asyncio.run(revoke(sys.argv[1])))
