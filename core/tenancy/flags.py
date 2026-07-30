"""Feature flag service (MVP-022) — see docs/21-platform/tenant-configuration.md.

Evaluation is an **allocation-light, I/O-free hot path**: `eval()` reads an in-memory
`Snapshot` (never the DB/Redis) and returns a value with provenance. The snapshot is loaded
from `feature_flags` + `flag_rules` and swapped **atomically** every ~30s (a single
reference assignment → readers never see a torn snapshot). Rule precedence is
user > tenant > pack > global; a rule applies only if its rollout gate passes, using a
**sticky** per-(org,key) bucket.

Kill-switch classes (`agent.*.enabled`, `pack.*.enabled`, `channel.*.enabled`) get a pubsub
push so a flip propagates in ≤2s (the subscriber loop is wired into the worker at MVP-028;
`publish_flag_change` emits it). Fail-safe: if no snapshot is available at boot (DB down, no
fallback file), kill-switch flags default **closed** (the risky behaviour is OFF).

Deviation: `bucket()` uses SHA-256 rather than murmur3 (no new dependency); it only needs to
be deterministic + well-distributed, which it is.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Precedence: lower rank wins first.
_SCOPE_RANK = {"user": 0, "tenant": 1, "pack": 2, "global": 3}
KILL_SWITCH_PREFIXES = ("agent.", "pack.", "channel.")


def is_kill_switch(key: str) -> bool:
    """Kill-class flags (agent/pack/channel *.enabled) fail closed and get pubsub push."""
    return key.endswith(".enabled") and key.startswith(KILL_SWITCH_PREFIXES)


def _fail_closed_default(key: str) -> Any:
    # Kill-switch closed == the risky behaviour disabled; convenience flags default off.
    return False


def bucket(org_id: UUID | str, key: str) -> int:
    """Sticky bucket in [0,100) for (org, flag) — stable across evaluations and processes."""
    digest = hashlib.sha256(f"{org_id}:{key}".encode()).hexdigest()
    return int(digest[:8], 16) % 100


@dataclass(frozen=True)
class Ctx:
    org_id: UUID | str
    user_id: UUID | str | None = None
    pack: str | None = None


@dataclass(frozen=True)
class Rule:
    scope: str
    scope_ref: str | None
    rollout_pct: int | None
    value: Any
    precedence: int


@dataclass(frozen=True)
class FlagDef:
    key: str
    flag_type: str
    default_value: Any
    tier: int
    rules: tuple[Rule, ...]  # pre-sorted by (scope rank, precedence)


@dataclass(frozen=True)
class Snapshot:
    flags: dict[str, FlagDef] = field(default_factory=dict)
    loaded_at: float = 0.0


@dataclass(frozen=True)
class FlagValue:
    value: Any
    source: str


def _matches(rule: Rule, ctx: Ctx) -> bool:
    if rule.scope == "global":
        return True
    if rule.scope == "tenant":
        return rule.scope_ref == str(ctx.org_id)
    if rule.scope == "user":
        return ctx.user_id is not None and rule.scope_ref == str(ctx.user_id)
    if rule.scope == "pack":
        return ctx.pack is not None and rule.scope_ref == ctx.pack
    return False


def _rollout_ok(rule: Rule, ctx: Ctx, key: str) -> bool:
    if rule.rollout_pct is None:
        return True
    return bucket(ctx.org_id, key) < rule.rollout_pct


def eval(snapshot: Snapshot, key: str, ctx: Ctx) -> FlagValue:  # noqa: A001 - domain verb
    """Evaluate `key` for `ctx` against `snapshot`. Pure + I/O-free (hot path)."""
    fd = snapshot.flags.get(key)
    if fd is None:
        # Unknown flag → fail-safe (kill-class closed).
        return FlagValue(_fail_closed_default(key), source="fallback")
    for rule in fd.rules:  # already precedence-sorted
        if _matches(rule, ctx) and _rollout_ok(rule, ctx, key):
            return FlagValue(rule.value, source=rule.scope)
    return FlagValue(fd.default_value, source="default")


# ---- Snapshot load + atomic swap -------------------------------------------

_current: Snapshot = Snapshot()


def get_snapshot() -> Snapshot:
    return _current


def set_snapshot(snapshot: Snapshot) -> None:
    """Atomic swap — a single reference assignment; readers see old or new, never torn."""
    global _current
    _current = snapshot


async def load_snapshot(session: AsyncSession) -> Snapshot:
    """Build a Snapshot from the DB (called by the ~30s refresher and at boot)."""
    flag_rows = (
        await session.execute(
            text("SELECT id, key, flag_type, default_value, tier FROM feature_flags")
        )
    ).mappings().all()
    rule_rows = (
        await session.execute(
            text(
                "SELECT flag_id, scope, scope_ref, rollout_pct, value, precedence FROM flag_rules"
            )
        )
    ).mappings().all()

    rules_by_flag: dict[Any, list[Rule]] = {}
    for r in rule_rows:
        rules_by_flag.setdefault(r["flag_id"], []).append(
            Rule(
                scope=r["scope"], scope_ref=r["scope_ref"], rollout_pct=r["rollout_pct"],
                value=r["value"], precedence=r["precedence"],
            )
        )

    flags: dict[str, FlagDef] = {}
    for f in flag_rows:
        rules = sorted(
            rules_by_flag.get(f["id"], []),
            key=lambda r: (_SCOPE_RANK.get(r.scope, 99), r.precedence),
        )
        flags[f["key"]] = FlagDef(
            key=f["key"], flag_type=f["flag_type"], default_value=f["default_value"],
            tier=f["tier"], rules=tuple(rules),
        )
    return Snapshot(flags=flags, loaded_at=time.time())


# ---- Boot fallback file -----------------------------------------------------


def persist_snapshot(snapshot: Snapshot, path: Path) -> None:
    """Write a cold-start fallback file (shipped in the image, refreshed at runtime)."""
    data = {
        key: {
            "flag_type": fd.flag_type, "default_value": fd.default_value, "tier": fd.tier,
            "rules": [
                {
                    "scope": r.scope, "scope_ref": r.scope_ref, "rollout_pct": r.rollout_pct,
                    "value": r.value, "precedence": r.precedence,
                }
                for r in fd.rules
            ],
        }
        for key, fd in snapshot.flags.items()
    }
    path.write_text(json.dumps(data))


def load_fallback(path: Path) -> Snapshot:
    """Load the fallback snapshot at cold start; empty snapshot if the file is absent
    (→ kill-switch flags fail closed)."""
    if not path.is_file():
        return Snapshot()
    data = json.loads(path.read_text())
    flags = {
        key: FlagDef(
            key=key, flag_type=d["flag_type"], default_value=d["default_value"], tier=d["tier"],
            rules=tuple(
                Rule(
                    scope=r["scope"], scope_ref=r["scope_ref"], rollout_pct=r["rollout_pct"],
                    value=r["value"], precedence=r["precedence"],
                )
                for r in d["rules"]
            ),
        )
        for key, d in data.items()
    }
    return Snapshot(flags=flags, loaded_at=time.time())


# ---- Kill-switch pubsub -----------------------------------------------------

FLAGS_CHANNEL = "flags:changed"


async def publish_flag_change(redis: Any, key: str) -> None:
    """Push a flag change so subscribers reload within ≤2s (kill-switch fast path)."""
    await redis.publish(FLAGS_CHANNEL, key)
