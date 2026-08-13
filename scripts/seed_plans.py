"""Materialise the canonical Recover / Grow / Scale presets into `billing_plans` (PLAN-3).

Idempotent: identity is `config.preset_key`, so re-running never duplicates a plan and never touches
a row that lacks one — every legacy plan and every operator-created plan is invisible to it.

Requires a connection with **global** visibility of `billing_subscriptions` (BYPASSRLS or
superuser). That is a safety requirement, not a convenience: the table is FORCE-RLS, so an ordinary
application role sees zero rows and would report every plan as never-sold, letting the seeder
rewrite a plan a merchant already bought.

    uv run python scripts/seed_plans.py --dry-run
    uv run python scripts/seed_plans.py
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from core.billing.presets import InsufficientVisibility, apply_presets
from core.common.config import get_settings


async def _run(dry_run: bool) -> int:
    settings = get_settings()
    # The migrator URL is the one that legitimately holds global visibility; the privilege itself
    # is asserted inside `apply_presets`, so a mis-set URL fails loudly rather than silently.
    engine = create_async_engine(settings.database_migrator_url)
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            try:
                outcomes = await apply_presets(session, dry_run=dry_run)
            except InsufficientVisibility as exc:
                print(f"REFUSED: {exc}")
                return 2
            if not dry_run:
                await session.commit()
    finally:
        await engine.dispose()

    failed = False
    for o in outcomes:
        detail = f"  ({o.detail})" if o.detail else ""
        print(f"  {o.preset_key:16} {o.action:14} {o.plan_id or '':38}{detail}")
        failed = failed or o.action == "error"
    print("dry run — nothing written" if dry_run else "done")
    return 1 if failed else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change, write nothing")
    raise SystemExit(asyncio.run(_run(ap.parse_args().dry_run)))


if __name__ == "__main__":
    main()
