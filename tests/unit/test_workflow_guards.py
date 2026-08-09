"""Workflow guard library (MVP-072) — pure logic (ref parsing, injection, helpers).

The predicate evaluation against real L2/L3 rows lives in the integration suite
(`tests/integration/test_workflow_guards_db.py`); here we pin the grammar-facing surface.
"""

from __future__ import annotations

import pytest

from core.workflows.guards import (
    GUARD_NAMES,
    GuardRef,
    GuardResult,
    UnknownGuard,
    first_block,
    inject_mandated_guards,
    parse_guard_ref,
)


def test_parse_bare_guard() -> None:
    assert parse_guard_ref("not_suppressed") == GuardRef("not_suppressed", ())


def test_parse_guard_with_args() -> None:
    assert parse_guard_ref("touch_cap(3, 30d)") == GuardRef("touch_cap", ("3", "30d"))
    assert parse_guard_ref("consent_valid(marketing)") == GuardRef("consent_valid", ("marketing",))


def test_unknown_guard_name_rejected() -> None:
    with pytest.raises(UnknownGuard):
        parse_guard_ref("delete_everything")


def test_malformed_guard_rejected() -> None:
    with pytest.raises(UnknownGuard):
        parse_guard_ref("touch cap 3")


def test_all_seven_core_guards_recognised() -> None:
    assert GUARD_NAMES == {
        "consent_valid", "not_suppressed", "within_send_window", "touch_cap",
        "budget_ok", "flag_on", "tier_max",
    }
    for name in GUARD_NAMES:
        assert parse_guard_ref(name).name == name


def test_render_roundtrip() -> None:
    assert GuardRef("touch_cap", ("3", "30d")).render() == "touch_cap(3, 30d)"
    assert GuardRef("not_suppressed").render() == "not_suppressed"


def test_inject_adds_missing_mandated() -> None:
    got = inject_mandated_guards([GuardRef("budget_ok")], [GuardRef("not_suppressed")])
    assert got == [GuardRef("budget_ok"), GuardRef("not_suppressed")]


def test_inject_is_name_keyed_and_idempotent() -> None:
    declared = [GuardRef("not_suppressed"), GuardRef("touch_cap", ("3", "30d"))]
    # A mandated not_suppressed (even with no args) is not duplicated — keyed by name.
    got = inject_mandated_guards(declared, [GuardRef("not_suppressed")])
    assert [g.name for g in got].count("not_suppressed") == 1
    assert got == declared


def test_first_block_returns_first_failure() -> None:
    results = [
        GuardResult(True, "consent_valid(marketing)"),
        GuardResult(False, "not_suppressed", "guard not_suppressed blocked"),
        GuardResult(False, "within_send_window"),
    ]
    blocked = first_block(results)
    assert blocked is not None and blocked.guard == "not_suppressed"


def test_first_block_none_when_all_pass() -> None:
    assert first_block([GuardResult(True, "budget_ok"), GuardResult(True, "tier_max(1)")]) is None
