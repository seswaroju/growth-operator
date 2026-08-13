"""Typed plan config + promotion window semantics (PLAN-2). Pure, no DB."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.tenancy.plan_config import (
    CURRENT_SCHEMA_VERSION,
    PromotionDef,
    parse_plan_config,
    parse_promotions,
)

T0 = datetime(2026, 3, 1, tzinfo=UTC)
T1 = datetime(2026, 4, 1, tzinfo=UTC)


def _promo(**kw: object) -> PromotionDef:
    base = {"capability_key": "campaigns.whatsapp", "starts_at": T0, "ends_at": T1}
    return PromotionDef.model_validate({**base, **kw})


# ---- Mode marker --------------------------------------------------------------------------------


def test_absent_marker_is_a_legacy_plan() -> None:
    cfg = parse_plan_config({"agents": ["concierge"]})
    assert cfg.is_structured is False


def test_version_1_is_structured() -> None:
    cfg = parse_plan_config({"entitlement_schema_version": 1, "entitlements": ["catalog"]})
    assert cfg.is_structured and cfg.is_known_schema
    assert cfg.entitlements == ["catalog"]


def test_a_future_version_is_structured_but_unknown() -> None:
    """We refuse to guess a schema we do not implement."""
    cfg = parse_plan_config({"entitlement_schema_version": 2, "entitlements": ["catalog"]})
    assert cfg.is_structured and not cfg.is_known_schema


def test_a_typo_in_the_entitlements_key_cannot_flip_mode() -> None:
    """`entitlments` is preserved (extra=allow) but must not make a structured plan look legacy."""
    cfg = parse_plan_config({"entitlement_schema_version": 1, "entitlments": ["catalog"]})
    assert cfg.is_structured is True
    assert cfg.entitlements is None          # → resolver fails closed
    assert (cfg.model_extra or {})["entitlments"] == ["catalog"]  # preserved, no authority


def test_unknown_operator_keys_survive_a_round_trip() -> None:
    cfg = parse_plan_config({"note": "renewal discount agreed", "agents": []})
    assert (cfg.model_extra or {})["note"] == "renewal discount agreed"


def test_a_malformed_config_stays_recognised_as_structured() -> None:
    """A broken structured body must fail closed, not silently fall back to legacy features."""
    cfg = parse_plan_config({"entitlement_schema_version": 1, "entitlements": "not-a-list"})
    assert cfg.is_structured is True
    assert cfg.entitlements is None


def test_junk_config_values_degrade_to_empty_legacy() -> None:
    for raw in (None, 42, "[]", "{not json", ["a"]):
        cfg = parse_plan_config(raw)
        assert cfg.is_structured is False and cfg.entitlements is None


def test_a_json_string_config_is_parsed() -> None:
    cfg = parse_plan_config('{"entitlement_schema_version": 1, "entitlements": ["catalog"]}')
    assert cfg.is_known_schema and cfg.entitlements == ["catalog"]


# ---- Promotion windows: absolute, UTC, start-inclusive, end-exclusive ---------------------------


def test_before_the_window_grants_nothing() -> None:
    assert _promo().active_at(T0 - timedelta(microseconds=1)) is False


def test_the_exact_start_instant_grants() -> None:
    assert _promo().active_at(T0) is True


def test_one_instant_before_the_end_grants() -> None:
    assert _promo().active_at(T1 - timedelta(microseconds=1)) is True


def test_the_exact_end_instant_does_not_grant() -> None:
    """End exclusive."""
    assert _promo().active_at(T1) is False


def test_after_the_window_grants_nothing() -> None:
    assert _promo().active_at(T1 + timedelta(days=365)) is False


def test_an_open_ended_promotion_never_expires() -> None:
    assert _promo(ends_at=None).active_at(T1 + timedelta(days=3650)) is True


def test_disabled_grants_nothing_even_inside_the_window() -> None:
    assert _promo(enabled=False).active_at(T0 + timedelta(days=1)) is False


def test_a_non_utc_offset_is_normalised_not_rejected() -> None:
    p = PromotionDef.model_validate({
        "capability_key": "catalog", "starts_at": "2026-03-01T05:30:00+05:30"})
    assert p.starts_at == datetime(2026, 3, 1, 0, 0, tzinfo=UTC)   # IST midnight → 00:00 UTC


# ---- Per-entry tolerance ------------------------------------------------------------------------


def test_a_naive_timestamp_is_rejected_rather_than_assumed_utc() -> None:
    valid, errors = parse_promotions([{"capability_key": "catalog", "starts_at": "2026-03-01"}])
    assert valid == [] and len(errors) == 1


def test_one_bad_promotion_does_not_invalidate_the_others() -> None:
    valid, errors = parse_promotions([
        {"capability_key": "catalog", "starts_at": T0.isoformat()},
        {"capability_key": "broken"},                                  # missing starts_at
        {"capability_key": "landing_pages", "starts_at": T0.isoformat()},
    ])
    assert [p.capability_key for p in valid] == ["catalog", "landing_pages"]
    assert len(errors) == 1 and "broken" in errors[0]


def test_an_unknown_promotion_field_is_refused() -> None:
    """Strict: a typo in a promotion must never silently widen access."""
    valid, errors = parse_promotions([
        {"capability_key": "catalog", "starts_at": T0.isoformat(), "for_ever": True}])
    assert valid == [] and len(errors) == 1


def test_schema_version_constant_is_one() -> None:
    assert CURRENT_SCHEMA_VERSION == 1
