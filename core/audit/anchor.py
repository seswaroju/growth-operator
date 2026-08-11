"""Audit-chain anchoring (MVP-071) — external tamper-evidence for the per-org audit hash chains.

Each org's `audit_log` is a hash chain (`entry_hash = sha256(prev_hash + canonical fields)`), so
tampering is detectable **inside** the DB. But an attacker with full DB access could rewrite the
whole chain and recompute every hash so it stays internally consistent. Anchoring closes that gap:
periodically snapshot each org's chain **head** (`seq` + `entry_hash`) into an append-only file the
operator keeps **outside** the DB (a private git repo). Later, `verify_against_anchor` compares the
anchored head hashes against the live DB — a mismatch proves the history was rewritten or truncated
after the anchor. `audit_log` is FORCE-RLS, so heads are read one org at a time under its context.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.middleware import org_scoped_session

logger = logging.getLogger("core.audit.anchor")


async def chain_heads() -> list[dict[str, Any]]:
    """Each org's audit-chain head (its max-`seq` entry): `{org_id, seq, entry_hash}`. Orgs with no
    audit rows are skipped. Read per-org because `audit_log` is FORCE-RLS."""
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        org_ids = (await s.execute(text("SELECT id FROM organizations"))).scalars().all()
    heads: list[dict[str, Any]] = []
    for org_id in org_ids:
        async with org_scoped_session(org_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT seq, entry_hash FROM audit_log WHERE org_id = :o "
                        "ORDER BY seq DESC LIMIT 1"
                    ),
                    {"o": str(org_id)},
                )
            ).mappings().first()
        if row is not None:
            heads.append({
                "org_id": str(org_id), "seq": int(row["seq"]),
                "entry_hash": str(row["entry_hash"]),
            })
    return heads


async def build_anchor() -> dict[str, Any]:
    """A point-in-time anchor record: every org's chain head + the timestamp."""
    heads = await chain_heads()
    return {"anchored_at": datetime.now(UTC).isoformat(), "org_count": len(heads), "heads": heads}


def write_anchor(record: dict[str, Any], path: str | Path) -> None:
    """Append one anchor record as a JSON line to the append-only anchor file (creating parents)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")


def read_anchors(path: str | Path) -> list[dict[str, Any]]:
    """Every anchor record in the file, oldest first (`[]` if the file is absent)."""
    p = Path(path)
    if not p.is_file():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


@dataclass(frozen=True)
class Discrepancy:
    """A head whose live hash no longer matches the anchor (rewritten, or `current=None` = gone)."""

    org_id: str
    seq: int
    anchored: str
    current: str | None


async def verify_against_anchor(record: dict[str, Any]) -> list[Discrepancy]:
    """Compare one anchor record's head hashes against the live DB. Empty list ⇒ intact; any
    `Discrepancy` ⇒ the chain was rewritten or truncated after the anchor was taken."""
    problems: list[Discrepancy] = []
    for head in record.get("heads", []):
        async with org_scoped_session(UUID(head["org_id"])) as s:
            current = (
                await s.execute(
                    text("SELECT entry_hash FROM audit_log WHERE org_id = :o AND seq = :s"),
                    {"o": head["org_id"], "s": head["seq"]},
                )
            ).scalar_one_or_none()
        if current != head["entry_hash"]:
            problems.append(
                Discrepancy(head["org_id"], int(head["seq"]), head["entry_hash"], current)
            )
    return problems


async def run_audit_anchor() -> None:
    """Scheduler entry (daily): snapshot every org's chain head to the append-only anchor file the
    operator keeps in a private git repo. No-op (logs) when `audit_anchor_path` is unset."""
    path = get_settings().audit_anchor_path
    if not path:
        logger.info("audit anchoring not configured (audit_anchor_path unset) — skipping")
        return
    record = await build_anchor()
    write_anchor(record, path)
    logger.info("audit anchor written: %d org head(s) -> %s", record["org_count"], path)


def register_jobs() -> None:
    """Register the daily audit anchor (02:00 UTC)."""
    from core.events import scheduler as sched

    sched.register("audit_anchor", "0 2 * * *", run_audit_anchor)
