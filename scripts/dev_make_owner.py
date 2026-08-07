"""Create a store + owner membership for an email — local dev, to demo the customer app.

Usage:  uv run python scripts/dev_make_owner.py <email> [store name]
   or:  make make-owner EMAIL=you@store.com STORE="Ratna Gold"

Creates the user (if needed), a store, and an **owner** membership, so signing in with that email
lands straight in the full owner dashboard (the OTP login mints a token carrying the org + role).
Local dev convenience only — the real path is org onboarding.
"""

from __future__ import annotations

import asyncio
import sys
import uuid

import asyncpg

from core.common.config import get_settings


async def make_owner(email: str, store: str) -> int:
    dsn = get_settings().database_migrator_url.replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    try:
        user_id = await conn.fetchval("SELECT id FROM users WHERE email = $1", email)
        if user_id is None:
            user_id = uuid.uuid4()
            await conn.execute("INSERT INTO users (id, email) VALUES ($1, $2)", user_id, email)
        existing = await conn.fetchval(
            "SELECT o.name FROM user_orgs uo JOIN organizations o ON o.id = uo.org_id "
            "WHERE uo.user_id = $1 LIMIT 1", user_id)
        if existing is not None:
            print(f"{email} already belongs to a store ({existing!r}). Sign in to use it.")
            return 0
        org_id = uuid.uuid4()
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1, $2)", org_id, store)
        await conn.execute(
            "INSERT INTO user_orgs (user_id, org_id, role) VALUES ($1, $2, 'owner')",
            user_id, org_id)
        print(f"OK — {email} now owns {store!r} ({org_id}). "
              f"Sign in with {email} to open the owner dashboard.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: uv run python scripts/dev_make_owner.py <email> [store name]")
        raise SystemExit(2)
    store_name = sys.argv[2] if len(sys.argv) > 2 else "Demo Jewellers"
    raise SystemExit(asyncio.run(make_owner(sys.argv[1], store_name)))
