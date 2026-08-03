"""Policy-engine determinism + compile cache (MVP-065) — pure, no DB.

The decision must be a pure function of the *set* of matching rules, independent of the order they
arrive in. `select_decision` is exercised under 10k shuffled orderings; the CEL compile cache is
checked to reuse programs.
"""

from __future__ import annotations

import random
import time
import uuid

from core.approvals import engine
from core.approvals.engine import ActionContext, Contributor, select_decision


def _contribs() -> list[Contributor]:
    return [
        (0, "r-a", [], None, "hold", None),
        (2, "r-b", ["owner"], 3600, "hold", "whatsapp"),
        (1, "r-c", [], None, "hold", None),
        (3, "r-d", ["owner", "founder"], 1800, "cancel", "push"),
        (2, "r-e", [], None, "safe_default", None),
    ]


def test_max_tier_wins_and_matched_sorted() -> None:
    contribs = _contribs()
    matched = [c[1] for c in contribs]
    d = select_decision(contribs, matched)
    assert d.tier == 3
    assert d.approver_chain == ["owner", "founder"]  # the winning rule's chain
    assert d.matched_rules == sorted(matched)


def test_determinism_over_10k_shuffles() -> None:
    contribs = _contribs()
    matched = [c[1] for c in contribs]
    baseline = select_decision(list(contribs), list(matched))
    rng = random.Random(1234)
    for _ in range(10_000):
        shuffled = list(contribs)
        rng.shuffle(shuffled)
        d = select_decision(shuffled, [c[1] for c in shuffled])
        assert d.tier == baseline.tier
        assert d.approver_chain == baseline.approver_chain
        assert d.on_timeout == baseline.on_timeout
        assert d.matched_rules == baseline.matched_rules


def test_tie_break_is_stable() -> None:
    # Two tier-2 rules: the stable sort key (rule id) decides deterministically.
    a: Contributor = (2, "r-b", ["b"], None, "hold", None)
    b: Contributor = (2, "r-e", ["e"], None, "hold", None)
    assert select_decision([a, b], ["r-b", "r-e"]).approver_chain == ["e"]  # max key 'r-e'
    assert select_decision([b, a], ["r-e", "r-b"]).approver_chain == ["e"]  # order-independent


def test_empty_contributors_fail_safe_to_approval() -> None:
    d = select_decision([], [])
    assert d.tier == engine.DEFAULT_UNKNOWN_TIER and d.matched_rules == []


def test_cel_compile_cache_reuses_program() -> None:
    engine._PROGRAM_CACHE.clear()
    act = engine._activation(engine.ActionContext(org_id=__import__("uuid").uuid4(),
                                                  action_type="x", amount_minor=100))
    assert engine._matches("amount_minor > 50", act) is True
    assert "amount_minor > 50" in engine._PROGRAM_CACHE
    prog = engine._PROGRAM_CACHE["amount_minor > 50"]
    engine._matches("amount_minor > 50", act)  # second call
    assert engine._PROGRAM_CACHE["amount_minor > 50"] is prog  # same cached program


def test_evaluation_budget_p95(capsys: object) -> None:
    """Non-blocking benchmark: with programs compiled once, matching + selection over a realistic
    rule set should sit well under the 5 ms evaluation budget. Reports p95; asserts only a loose
    sanity bound so CI warns rather than fails (per the ticket)."""
    ctx = ActionContext(org_id=uuid.uuid4(), action_type="messages.send", amount_minor=60000)
    act = engine._activation(ctx)
    exprs = [f"amount_minor > {i * 1000}" for i in range(20)]
    for e in exprs:  # warm the compile cache
        engine._matches(e, act)

    samples = []
    for _ in range(1000):
        t0 = time.perf_counter()
        contribs = [
            (i % 4, f"r{i}", [], None, "hold", None)
            for i, e in enumerate(exprs) if engine._matches(e, act)
        ]
        select_decision(contribs, [c[1] for c in contribs])
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    p95 = samples[int(len(samples) * 0.95)]
    print(f"\n[approval-engine] evaluation p95 = {p95:.3f} ms (budget 5 ms, warn-only)")
    assert p95 < 25.0  # generous CI bound; the 5 ms target is a warn, not a gate
