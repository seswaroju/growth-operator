#!/usr/bin/env python
"""Re-walk an org's audit chain and report the first break (MVP-024).

Operator / DR tool (audit-logging.md, RB-02 step 7). Reads with the migrator (owner)
connection so it can inspect any org, and reports the exact seq of the first break — a
tamper, a broken hash link, or a sequence gap.

    uv run python scripts/audit-verify.py --org <uuid> [--from-seq N]

Exit 0 = chain intact (or empty); exit 1 = a break was found.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import asyncpg

from core.audit import ChainRecord, verify_chain
from core.common.config import get_settings

_QUERY = """
SELECT seq, actor_type, actor_id, action, resource, payload,
       prev_hash, entry_hash, permission_manifest_hash
FROM audit_log
WHERE org_id = $1::uuid AND seq >= $2
ORDER BY seq
"""


def _as_dict(payload: Any) -> dict[str, Any]:
    return json.loads(payload) if isinstance(payload, str) else dict(payload or {})


async def _fetch(org: str, from_seq: int) -> list[ChainRecord]:
    dsn = get_settings().database_migrator_url.replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(_QUERY, org, from_seq)
    finally:
        await conn.close()
    return [
        ChainRecord(
            seq=r["seq"],
            actor_type=r["actor_type"],
            actor_id=r["actor_id"],
            action=r["action"],
            resource=r["resource"],
            payload=_as_dict(r["payload"]),
            prev_hash=r["prev_hash"],
            entry_hash=r["entry_hash"],
            permission_manifest_hash=r["permission_manifest_hash"],
        )
        for r in rows
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify an org's audit hash chain.")
    parser.add_argument("--org", required=True, help="org_id (uuid)")
    parser.add_argument("--from-seq", type=int, default=1, help="start seq (default 1)")
    args = parser.parse_args()

    records = asyncio.run(_fetch(args.org, args.from_seq))
    if not records:
        print(f"audit-verify: no entries for org {args.org} from seq {args.from_seq}")
        return 0

    break_at = verify_chain(records)
    span = f"seq {records[0].seq}..{records[-1].seq}"
    if break_at is None:
        print(f"audit-verify: OK — {len(records)} entries, chain intact ({span})")
        return 0
    print(
        f"audit-verify: CHAIN BREAK at seq {break_at} for org {args.org}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
