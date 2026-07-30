"""Audit chain writer + capability verification (MVP-024, ADR-007).

Implements docs/21-platform/audit-logging.md: a per-org, append-only, hash-chained log.
`write()` runs **in the caller's transaction, before any external side effect** (log-then-
act), and returns an `AuditId` capability valid for 10 minutes that side-effecting adapters
(channel send, payments, exports) must present to `verify_capability()` before acting.
Failed/succeeded effects append a compensating outcome entry, so the chain records intent
AND outcome.

Serialization: a per-org advisory transaction lock (`pg_advisory_xact_lock`, keyed by a
64-bit hash of `org_id`) serializes writes within an org while letting different orgs run
fully in parallel — and, unlike a `FOR UPDATE` head-lock, it also serializes the very first
(genesis) write. `UNIQUE(org_id, seq)` is the backstop against gaps/dupes.

Canonicalization: `canonical_json` sorts keys and uses compact separators — a JCS-compatible
form that is stable across runs for the value types audit payloads use (strings, ints, ids,
nested objects; **no floats** — money is integer minor units). The entry hash covers every
immutable field, so tampering with any of them breaks the chain.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CAPABILITY_TTL = timedelta(minutes=10)
GENESIS_PREV_HASH = ""  # prev_hash of the first row in an org's chain


def canonical_json(obj: Any) -> str:
    """Deterministic JSON (sorted keys, compact) — the bytes that get hashed."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _entry_hash(
    *,
    prev_hash: str,
    seq: int,
    actor_type: str,
    actor_id: str | None,
    action: str,
    resource: str | None,
    payload: dict[str, Any],
    permission_manifest_hash: str | None,
) -> str:
    """sha256(prev_hash + canonical_json(immutable fields)). Shared by write + verify."""
    body = canonical_json(
        {
            "seq": seq,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "action": action,
            "resource": resource,
            "payload": payload,
            "permission_manifest_hash": permission_manifest_hash,
        }
    )
    return hashlib.sha256((prev_hash + body).encode()).hexdigest()


@dataclass
class AuditEntry:
    org_id: UUID
    actor_type: str
    action: str
    actor_id: str | None = None
    resource: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    permission_manifest_hash: str | None = None


@dataclass
class AuditId:
    """A capability: the audit row id, valid until `expires_at` (10 minutes)."""

    id: UUID
    expires_at: datetime


async def write(session: AsyncSession, entry: AuditEntry) -> AuditId:
    """Append `entry` to its org's chain, in the caller's transaction. Returns the capability.

    Must be called inside the intent transaction and BEFORE the external side effect.
    """
    org = str(entry.org_id)
    # One round-trip: take this org's advisory chain lock (serializes writes within the org,
    # never across orgs) AND set the tenant context so the org's rows are visible under RLS.
    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtextextended(:org, 0)), "
            "set_config('app.org_id', :org, true)"
        ),
        {"org": org},
    )

    head = (
        await session.execute(
            text(
                "SELECT seq, entry_hash FROM audit_log WHERE org_id = :org "
                "ORDER BY seq DESC LIMIT 1"
            ),
            {"org": org},
        )
    ).mappings().first()
    seq = (head["seq"] + 1) if head else 1
    prev_hash = head["entry_hash"] if head else GENESIS_PREV_HASH

    entry_hash = _entry_hash(
        prev_hash=prev_hash,
        seq=seq,
        actor_type=entry.actor_type,
        actor_id=entry.actor_id,
        action=entry.action,
        resource=entry.resource,
        payload=entry.payload,
        permission_manifest_hash=entry.permission_manifest_hash,
    )

    row = (
        await session.execute(
            text(
                """
                INSERT INTO audit_log
                  (org_id, seq, actor_type, actor_id, action, resource, payload,
                   prev_hash, entry_hash, trace_id, permission_manifest_hash)
                VALUES
                  (:org, :seq, :actor_type, :actor_id, :action, :resource,
                   CAST(:payload AS jsonb),
                   :prev_hash, :entry_hash, :trace_id, :pmh)
                RETURNING id, created_at
                """
            ),
            {
                "org": org,
                "seq": seq,
                "actor_type": entry.actor_type,
                "actor_id": entry.actor_id,
                "action": entry.action,
                "resource": entry.resource,
                "payload": canonical_json(entry.payload),
                "prev_hash": prev_hash,
                "entry_hash": entry_hash,
                "trace_id": entry.trace_id,
                "pmh": entry.permission_manifest_hash,
            },
        )
    ).mappings().one()
    return AuditId(id=row["id"], expires_at=row["created_at"] + CAPABILITY_TTL)


async def verify_capability(
    session: AsyncSession,
    audit_id: UUID,
    *,
    action: str,
    resource: str | None,
    now: datetime | None = None,
) -> bool:
    """True iff `audit_id` exists, is <10min old, and matches `action`/`resource`.

    Side-effecting adapters call this before acting; a missing, expired, or mismatched
    capability must block the side effect.
    """
    now = now or datetime.now(UTC)
    row = (
        await session.execute(
            text("SELECT action, resource, created_at FROM audit_log WHERE id = :id"),
            {"id": audit_id},
        )
    ).mappings().first()
    if row is None:
        return False
    if row["created_at"] + CAPABILITY_TTL <= now:
        return False
    return row["action"] == action and row["resource"] == resource


async def write_outcome(
    session: AsyncSession, audit_id: UUID, outcome: str, detail: str | None = None
) -> AuditId:
    """Append a compensating `<action>:<outcome>` entry for a prior intent (succeeded/failed)."""
    row = (
        await session.execute(
            text(
                "SELECT org_id, actor_type, actor_id, action, resource "
                "FROM audit_log WHERE id = :id"
            ),
            {"id": audit_id},
        )
    ).mappings().first()
    if row is None:
        raise ValueError(f"unknown audit_id: {audit_id}")
    return await write(
        session,
        AuditEntry(
            org_id=row["org_id"],
            actor_type=row["actor_type"],
            actor_id=row["actor_id"],
            action=f"{row['action']}:{outcome}",
            resource=row["resource"],
            payload={"intent_audit_id": str(audit_id), "detail": detail},
        ),
    )


# ---- Chain verification (used by scripts/audit-verify.py) -------------------


@dataclass
class ChainRecord:
    seq: int
    actor_type: str
    actor_id: str | None
    action: str
    resource: str | None
    payload: dict[str, Any]
    prev_hash: str
    entry_hash: str
    permission_manifest_hash: str | None


def verify_chain(records: list[ChainRecord]) -> int | None:
    """Walk records (ordered by seq) and return the first seq where the chain breaks, or
    None if intact. Detects: a wrong entry hash (tamper), a broken prev/entry link, and a
    gap in the per-org sequence.

    The walk is seeded from the first record, so a partial slice (`--from-seq > 1`) verifies
    forward from a trusted starting point. When the slice includes genesis (`seq == 1`), the
    genesis `prev_hash` must be empty.
    """
    if not records:
        return None
    first = records[0]
    if first.seq == 1 and first.prev_hash != GENESIS_PREV_HASH:
        return 1  # invalid/tampered genesis
    expected_prev = first.prev_hash
    expected_seq = first.seq
    for rec in records:
        if rec.seq != expected_seq:
            return rec.seq  # gap or out-of-order
        if rec.prev_hash != expected_prev:
            return rec.seq  # broken link to the previous row
        recomputed = _entry_hash(
            prev_hash=rec.prev_hash,
            seq=rec.seq,
            actor_type=rec.actor_type,
            actor_id=rec.actor_id,
            action=rec.action,
            resource=rec.resource,
            payload=rec.payload,
            permission_manifest_hash=rec.permission_manifest_hash,
        )
        if recomputed != rec.entry_hash:
            return rec.seq  # tampered row
        expected_prev = rec.entry_hash
        expected_seq = rec.seq + 1
    return None
