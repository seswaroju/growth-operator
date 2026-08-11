"""Verify the live audit chains against the most recent anchor (MVP-071).

Reads the newest record from the anchor file (`audit_anchor_path`) and checks every org's anchored
chain head against the live DB. Exit 0 = intact; 1 = TAMPER (a head was rewritten or truncated after
the anchor); 2 = not configured / no anchors. Run it from the operator host, or in the private
anchor repo's CI, whenever you want assurance the audit log has not been rewritten.
"""

from __future__ import annotations

import asyncio
import sys

from core.audit import anchor
from core.common.config import get_settings


async def _main() -> int:
    path = get_settings().audit_anchor_path
    if not path:
        print("audit_anchor_path is not configured (set GROWTH_OPERATOR_AUDIT_ANCHOR_PATH)",
              file=sys.stderr)
        return 2
    records = anchor.read_anchors(path)
    if not records:
        print(f"no anchor records found in {path}", file=sys.stderr)
        return 2
    latest = records[-1]
    problems = await anchor.verify_against_anchor(latest)
    if not problems:
        print(f"OK — {latest['org_count']} org chain head(s) match the anchor "
              f"taken at {latest['anchored_at']}")
        return 0
    print(f"TAMPER DETECTED — {len(problems)} head(s) differ from the anchor:", file=sys.stderr)
    for p in problems:
        print(f"  org {p.org_id} seq {p.seq}: anchored {p.anchored[:12]}… now {p.current}",
              file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
