"""Remove leftover test fixtures from a LOCAL development database (DEMO-UX-1).

Test suites create disposable stores and plans. Most clean up after themselves; a suite that
crashed before its teardown, or one written before the shared helper owned its own cleanup, leaves
rows behind. They accumulate quietly until the operator console is unusable — the founder's
database had 1171 plans, 1010 from a single helper.

The ongoing leak is fixed at its source in `tests/conftest.py`. This removes what is already there.

**Development only, and it checks.** Three independent conditions must hold: `env=dev`, a database
URL that is not obviously remote, and ids named explicitly on the command line. Deleting a store is
not something a flag should be able to do by accident, so the refusals are deliberately hard to
argue past rather than convenient to bypass.

**Canonical plans are never touched.** Recover, Grow and Scale are code-managed commercial truth
(PLAN-3/PLAN-4); a cleanup script is not the right place to remove them, and if this script could,
someone would eventually run it in the wrong terminal.

**Referenced rows are refused, not force-deleted.** Anything with real conversation, message or
audit history is reported and skipped: it is more likely to be a demo tenant the founder built than
a stray fixture, and a cleanup tool that guesses wrong destroys work.

**Discovery and destruction are separate.** A dry run finds rows that *look* like fixtures by name
and prints their ids. Deleting requires naming those ids explicitly. Prefix matching is a good way
to find candidates and a bad way to authorise destruction: `Alpha`, `Beta` and `TB` are fixture
names today and are perfectly plausible names for a real store, and five history-table counts are
not enough evidence to delete somebody's tenant. So the script will not act on its own guess.

    uv run python scripts/dev_purge_fixtures.py --stores           # dry run — prints candidate ids
    uv run python scripts/dev_purge_fixtures.py --plan-ids a,b,c   # delete exactly these
    uv run python scripts/dev_purge_fixtures.py --store-ids d,e    # delete exactly these
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import asyncpg

from core.common.config import get_settings

#: Names the canonical presets carry. Matched exactly, never by prefix.
CANONICAL_PLANS = ("Recover", "Grow", "Scale")

#: Name prefixes that only a test fixture produces. Deliberately an allow-list of known fixture
#: shapes rather than "anything that looks disposable" — a pattern broad enough to catch every
#: fixture is broad enough to catch a real store called "Test Jewellers".
FIXTURE_PLAN_PREFIXES = ("ent-", "CampPlan-", "lpub-", "Plan-", "tc-plan-", "p-", "re-", "ag-")
FIXTURE_ORG_PREFIXES = ("lpub-", "tc-", "re-", "ag-", "rec-", "ow-", "bs-", "GhostStore",
                        "Alpha", "Beta", "TB", "IX", "PA", "PS", "AR", "AP")

#: Tables whose presence means a store holds work someone might want. Checked before deleting an
#: org, so a demo tenant with a real conversation survives.
HISTORY_TABLES = ("messages", "conversations", "audit_log", "recovery_attempts", "campaigns")


def _refuse_unless_dev() -> str:
    settings = get_settings()
    if settings.env != "dev":
        sys.exit(f"refusing: env is {settings.env!r}, not 'dev'. This script only runs locally.")
    dsn = settings.database_migrator_url.replace("+asyncpg", "")
    if not any(host in dsn for host in ("localhost", "127.0.0.1", "@postgres")):
        sys.exit("refusing: the database URL does not look local. Purge is for laptops only.")
    return dsn


async def _fixture_plans(
    conn: asyncpg.Connection, *, ignoring_orgs: list[object] | None = None
) -> list[asyncpg.Record]:
    """Fixture plans, with a count of the subscriptions that would still reference them.

    `ignoring_orgs` are stores this run is about to delete, so their subscriptions do not count as
    references. Without that, a fixture graph is unpurgeable: the plan is held by a subscription
    held by a store that exists only because the plan does. §4.3 allows purging an explicitly
    disposable graph, and this is what makes that possible without a force flag.
    """
    rows = await conn.fetch(
        "SELECT p.id, p.name, "
        "  (SELECT count(*) FROM billing_subscriptions s WHERE s.plan_id = p.id "
        "     AND NOT (s.org_id = ANY($1::uuid[]))) AS subs "
        "FROM billing_plans p ORDER BY p.created_at", list(ignoring_orgs or []))
    return [
        r for r in rows
        if r["name"] not in CANONICAL_PLANS
        and not r["name"].startswith(tuple(f"{c} · " for c in CANONICAL_PLANS))
        and r["name"].startswith(FIXTURE_PLAN_PREFIXES)
    ]


async def _fixture_orgs(conn: asyncpg.Connection) -> list[tuple[asyncpg.Record, int]]:
    rows = await conn.fetch("SELECT id, name FROM organizations ORDER BY created_at")
    out: list[tuple[asyncpg.Record, int]] = []
    for r in rows:
        if not r["name"].startswith(FIXTURE_ORG_PREFIXES):
            continue
        history = 0
        for table in HISTORY_TABLES:
            try:
                history += int(await conn.fetchval(
                    f"SELECT count(*) FROM {table} WHERE org_id = $1", r["id"]) or 0)  # noqa: S608
            except asyncpg.PostgresError:
                continue
        out.append((r, history))
    return out


def _parse_ids(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


async def main_async(
    *, stores: bool, plan_ids: list[str], store_ids: list[str]
) -> None:
    conn = await asyncpg.connect(_refuse_unless_dev())
    try:
        # Stores first: which ones go decides which plan references still count.
        org_rows = await _fixture_orgs(conn) if stores else []
        doomed_orgs = [r["id"] for r, h in org_rows if h == 0]
        if stores:
            dirty = [(r, h) for r, h in org_rows if h > 0]
            print(f"fixture stores found: {len(org_rows)}  (empty {len(doomed_orgs)}, "
                  f"with history {len(dirty)})")
            for r, h in dirty:
                print(f"  SKIP {r['name']}: {h} history row(s) — looks like real work")

        plans = await _fixture_plans(conn, ignoring_orgs=doomed_orgs)
        deletable = [p for p in plans if p["subs"] == 0]
        referenced = [p for p in plans if p["subs"] > 0]

        print(f"fixture plans found: {len(plans)}  (deletable {len(deletable)}, "
              f"still referenced {len(referenced)})")
        for p in referenced:
            print(f"  SKIP {p['name']}: {p['subs']} subscription(s) outside this purge")

        if not plan_ids and not store_ids:
            print("\nDRY RUN — nothing deleted.")
            print("To delete, name the ids explicitly (they are printed above):")
            if deletable:
                print("  --plan-ids " + ",".join(str(p["id"]) for p in deletable[:3])
                      + ("," if len(deletable) > 3 else ""))
            if doomed_orgs:
                print("  --store-ids " + ",".join(str(o) for o in doomed_orgs[:3])
                      + ("," if len(doomed_orgs) > 3 else ""))
            return

        # Only ids the founder named, and only if discovery already classified them as disposable.
        # The intersection matters: an explicit id is authorisation, and the discovery check is
        # still the safety net that refuses a store holding real conversation history.
        deletable_ids = {str(p["id"]) for p in deletable}
        doomed_ids = {str(o) for o in doomed_orgs}
        chosen_plans = [pid for pid in plan_ids if pid in deletable_ids]
        chosen_stores = [sid for sid in store_ids if sid in doomed_ids]
        for pid in plan_ids:
            if pid not in deletable_ids:
                print(f"  REFUSED plan {pid}: not a disposable fixture in this database")
        for sid in store_ids:
            if sid not in doomed_ids:
                print(f"  REFUSED store {sid}: not an empty fixture store in this database")
        if not chosen_plans and not chosen_stores:
            print("\nnothing to do — no named id passed the safety checks.")
            return
        deletable = [p for p in deletable if str(p["id"]) in set(chosen_plans)]
        doomed_orgs = [o for o in doomed_orgs if str(o) in set(chosen_stores)]

        # One transaction: a half-purged graph (plans gone, stores left) is worse than either
        # outcome, and would be tedious to reason about afterwards.
        async with conn.transaction():
            if doomed_orgs:
                # Subscriptions before organizations — the foreign key refuses otherwise, and a
                # subscription outliving its store would be worse than the row it replaced.
                await conn.execute(
                    "DELETE FROM billing_subscriptions WHERE org_id = ANY($1::uuid[])", doomed_orgs)
                await conn.execute(
                    "DELETE FROM organizations WHERE id = ANY($1::uuid[])", doomed_orgs)
            if deletable:
                await conn.execute("DELETE FROM billing_plans WHERE id = ANY($1::uuid[])",
                                   [p["id"] for p in deletable])
        print(f"\ndeleted {len(deletable)} plan(s) and {len(doomed_orgs)} store(s)")
        remaining = await conn.fetchval("SELECT count(*) FROM billing_plans")
        print(f"plans remaining: {remaining}")
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Purge leftover test fixtures (local dev only).")
    ap.add_argument("--stores", action="store_true",
                    help="include fixture stores in discovery")
    ap.add_argument("--plan-ids", default="",
                    help="comma-separated plan ids to DELETE (from a dry run)")
    ap.add_argument("--store-ids", default="",
                    help="comma-separated store ids to DELETE (from a dry run)")
    args = ap.parse_args()
    store_ids = _parse_ids(args.store_ids)
    asyncio.run(main_async(
        stores=args.stores or bool(store_ids),
        plan_ids=_parse_ids(args.plan_ids), store_ids=store_ids))


if __name__ == "__main__":
    main()
