"""Feature flag evaluation — pure, no DB (MVP-022).

Covers rule precedence, sticky bucket stability, the rollout gate, fail-closed defaults, the
atomic (torn-read-free) snapshot swap, and the boot fallback file.
"""

from __future__ import annotations

import asyncio

from core.tenancy import flags
from core.tenancy.flags import Ctx, FlagDef, Rule, Snapshot, bucket, eval, is_kill_switch


def _snapshot(*flag_defs: FlagDef) -> Snapshot:
    return Snapshot(flags={fd.key: fd for fd in flag_defs}, loaded_at=1e18)


def test_precedence_user_over_tenant_over_global() -> None:
    org, user = "org-1", "user-1"
    fd = FlagDef(
        key="x", flag_type="multivariate", default_value="D", tier=1,
        rules=(  # pre-sorted user > tenant > global
            Rule("user", user, None, "U", 100),
            Rule("tenant", org, None, "T", 100),
            Rule("global", None, None, "G", 100),
        ),
    )
    snap = _snapshot(fd)
    assert eval(snap, "x", Ctx(org, user)).value == "U"
    assert eval(snap, "x", Ctx(org, "someone-else")).value == "T"
    assert eval(snap, "x", Ctx("other-org", None)).value == "G"


def test_rollout_gate_uses_sticky_bucket() -> None:
    org, key = "org-2", "beta"
    b = bucket(org, key)
    on = FlagDef(key, "boolean", False, 1, (Rule("tenant", org, b + 1, True, 100),))
    off = FlagDef(key, "boolean", False, 1, (Rule("tenant", org, b, True, 100),))
    assert eval(_snapshot(on), key, Ctx(org)).value is True   # bucket < b+1 → in rollout
    assert eval(_snapshot(off), key, Ctx(org)).value is False  # bucket < b is false → default


def test_bucket_is_stable_and_in_range() -> None:
    for org in ("a", "b", "c-uuid"):
        for key in ("agent.enabled", "x.y"):
            v = bucket(org, key)
            assert 0 <= v < 100
            assert bucket(org, key) == v  # deterministic / sticky


def test_unknown_kill_switch_fails_closed() -> None:
    assert is_kill_switch("agent.concierge.enabled")
    assert not is_kill_switch("some.convenience")
    fv = eval(Snapshot(), "agent.concierge.enabled", Ctx("org"))
    assert fv.value is False and fv.source == "fallback"


async def test_snapshot_swap_is_atomic_no_torn_read() -> None:
    a = _snapshot(FlagDef("k", "multivariate", "A", 1, ()))
    b = _snapshot(FlagDef("k", "multivariate", "B", 1, ()))
    flags.set_snapshot(a)
    seen: set[str] = set()

    async def reader() -> None:
        for _ in range(500):
            seen.add(eval(flags.get_snapshot(), "k", Ctx("o")).value)
            await asyncio.sleep(0)

    async def swapper() -> None:
        for i in range(500):
            flags.set_snapshot(a if i % 2 else b)
            await asyncio.sleep(0)

    await asyncio.gather(reader(), reader(), swapper())
    assert seen <= {"A", "B"}  # only ever whole values — no torn/partial snapshot


def test_fallback_file_round_trip(tmp_path) -> None:
    snap = _snapshot(
        FlagDef("f", "boolean", True, 3, (Rule("tenant", "o", 50, False, 10),))
    )
    path = tmp_path / "flags.json"
    flags.persist_snapshot(snap, path)
    loaded = flags.load_fallback(path)
    assert loaded.flags["f"].default_value is True
    assert loaded.flags["f"].rules[0].rollout_pct == 50
    # Missing file → empty snapshot → kill-switch fails closed.
    empty = flags.load_fallback(tmp_path / "nope.json")
    assert eval(empty, "channel.whatsapp.enabled", Ctx("o")).value is False
