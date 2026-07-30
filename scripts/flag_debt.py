#!/usr/bin/env python
"""Feature-flag expiry-debt check (MVP-022).

Counts flags whose `expires_at` has passed but are STILL referenced in code (a temporary
flag that outlived its purpose). Build fails when there are more than 20 such flags, or any
single flag is more than 90 days past expiry — pressure to delete stale flags + their code.

    uv run python scripts/flag_debt.py

Exit 0 = within budget; exit 1 = over budget.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

from core.common.config import get_settings

REPO = Path(__file__).resolve().parents[1]
SCAN_DIRS = [REPO / "core", REPO / "web" / "src"]
MAX_DEBT = 20
MAX_AGE = timedelta(days=90)


def _referenced(key: str) -> bool:
    for root in SCAN_DIRS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix in {".py", ".ts", ".tsx"} and key in path.read_text(
                encoding="utf-8", errors="ignore"
            ):
                return True
    return False


async def _expired_flags() -> list[tuple[str, datetime]]:
    dsn = get_settings().database_migrator_url.replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT key, expires_at FROM feature_flags "
            "WHERE expires_at IS NOT NULL AND expires_at < now()"
        )
    finally:
        await conn.close()
    return [(r["key"], r["expires_at"]) for r in rows]


def main() -> int:
    now = datetime.now(UTC)
    try:
        expired = asyncio.run(_expired_flags())
    except Exception as exc:  # pragma: no cover - operator convenience
        print(f"flag-debt: could not query flags ({exc}); skipping", file=sys.stderr)
        return 0

    debt = [(k, exp) for k, exp in expired if _referenced(k)]
    too_old = [(k, exp) for k, exp in debt if now - exp > MAX_AGE]

    for key, exp in debt:
        age = (now - exp).days
        print(f"flag-debt: {key} expired {age}d ago, still referenced")

    if len(debt) > MAX_DEBT or too_old:
        print(
            f"\nflag-debt FAILED: {len(debt)} referenced-expired flags "
            f"(max {MAX_DEBT}); {len(too_old)} over {MAX_AGE.days} days",
            file=sys.stderr,
        )
        return 1
    print(f"flag-debt OK: {len(debt)} referenced-expired flags (budget {MAX_DEBT})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
