"""Grant a user platform-admin — the Growth Operator operator role (support-tickets track).

Usage:  uv run python scripts/grant_platform_admin.py <email> [--days N]
   or:  make grant-admin EMAIL=you@example.com

Adds the user to the `platform_admins` allowlist — the SOLE authority for the cross-tenant operator
console (the org-scoped `founder` role does NOT confer it; see core/tenancy/platform_admin.py). With
`--days N`, access **auto-expires** after N days (enterprise governance — cross-tenant access should
not linger); omit it for a non-expiring bootstrap operator. Re-running updates the expiry. Every
grant is recorded in the append-only `platform_access_log`. The user must have logged in once (OTP)
so a `users` row exists.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

import asyncpg

from core.common.config import get_settings


async def grant(email: str, days: int | None, role: str = "admin") -> int:
    dsn = get_settings().database_migrator_url.replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    try:
        user_id = await conn.fetchval("SELECT id FROM users WHERE email = $1", email)
        if user_id is None:
            print(f"No user with email {email!r}. Have them log in once (OTP) first, then re-run.")
            return 1
        expires_at = datetime.now(UTC) + timedelta(days=days) if days else None
        await conn.execute(
            "INSERT INTO platform_admins (user_id, note, expires_at, role) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (user_id) DO UPDATE SET note = EXCLUDED.note, "
            "  expires_at = EXCLUDED.expires_at, role = EXCLUDED.role",
            user_id, f"granted via scripts/grant_platform_admin.py for {email}", expires_at, role,
        )
        await conn.execute(
            "INSERT INTO platform_access_log (actor_user_id, action, detail) "
            "VALUES ($1, 'platform.admin.granted', $2::jsonb)",
            user_id,
            json.dumps({"email": email, "via": "script", "role": role,
                        "expires_at": expires_at.isoformat() if expires_at else None}),
        )
        window = f"expires {expires_at.date().isoformat()}" if expires_at else "no expiry"
        print(f"OK — {email} ({user_id}) is now a platform admin: role={role}, {window}.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Grant platform-admin (operator) access.")
    parser.add_argument("email")
    parser.add_argument("--days", type=int, default=None,
                        help="auto-expire access after N days (default: never)")
    parser.add_argument("--role", choices=["dev", "admin", "staff", "analyst"], default="admin",
                        help="operator role (default: admin)")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(grant(args.email, args.days, args.role)))
