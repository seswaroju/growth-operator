"""Structured entitlement resolver (PLAN-2) against real Postgres.

Covers the two paths that must not diverge: an **active legacy** plan reconstructs ENT-1a's
historical semantics, and a **structured** (`entitlement_schema_version: 1`) plan receives only what
it explicitly lists. Skips when the DB is unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.entitlements import resolve

T0 = datetime(2026, 3, 1, tzinfo=UTC)
T1 = datetime(2026, 4, 1, tzinfo=UTC)
LEGACY_BASELINE = {"conversations", "catalog", "customers", "ghost_recovery"}


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.billing_subscriptions')"))
    finally:
        await conn.close()


class World:
    """Helpers that build plans/subscriptions directly, so each test states its own situation."""

    def __init__(self, conn: asyncpg.Connection, tag: str) -> None:
        self.conn, self.tag, self.orgs = conn, tag, []
        self.bindings: list[uuid.UUID] = []

    async def org(self, vertical: str = "jewelry") -> uuid.UUID:
        oid = uuid.uuid4()
        await self.conn.execute(
            "INSERT INTO organizations (id, name, vertical) VALUES ($1,$2,$3)",
            oid, f"{self.tag}-{len(self.orgs)}", vertical)
        self.orgs.append(oid)
        return oid

    async def plan(self, *, features: list[str] | None = None, config: dict | None = None,
                   active: bool = True, staff: int = 5) -> uuid.UUID:
        pid = uuid.uuid4()
        await self.conn.execute(
            "INSERT INTO billing_plans (id, name, price_minor, active, features, config, "
            "max_managers, max_staff) VALUES ($1,$2,0,$3,$4::jsonb,$5::jsonb,2,$6)",
            pid, f"{self.tag}-plan-{uuid.uuid4().hex[:6]}", active,
            json.dumps(features or []), json.dumps(config or {}), staff)
        return pid

    async def subscribe(self, org: uuid.UUID, plan: uuid.UUID, status: str = "active") -> None:
        await self.conn.execute(
            "INSERT INTO billing_subscriptions (org_id, plan_id, status) VALUES ($1,$2,$3)",
            org, plan, status)

    async def install_pack(self, org: uuid.UUID, slug: str, status: str = "active") -> None:
        pid = await self.conn.fetchval("SELECT id FROM packs WHERE slug=$1", slug)
        if pid is None:
            pid = uuid.uuid4()
            await self.conn.execute(
                "INSERT INTO packs (id, slug, version, platform_api, manifest, bundle_uri, "
                "signature, status) VALUES ($1,$2,'1.0.0','1.0','{}'::jsonb,'x','x','published')",
                pid, slug)
        await self.conn.execute(
            "INSERT INTO pack_installations (org_id, pack_id, status) VALUES ($1,$2,$3)",
            org, pid, status)

    async def bind_agent(self, org: uuid.UUID, slug: str, status: str = "active") -> None:
        """Give the org an agent instance for `slug` — the tenant/pack support signal."""
        arch = await self.conn.fetchval("SELECT id FROM agent_archetypes WHERE slug=$1", slug)
        # Bind to a named pack, never `LIMIT 1`: other suites create packs too, and an
        # arbitrary pick made this fixture order-dependent.
        pack = await self.conn.fetchval("SELECT id FROM packs WHERE slug='jewelry'")
        if pack is None:
            pack = uuid.uuid4()
            await self.conn.execute(
                "INSERT INTO packs (id, slug, version, platform_api, manifest, bundle_uri, "
                "signature, status) VALUES ($1,'jewelry','1','1','{}'::jsonb,'x','x',"
                "'published')", pack)
        # (pack_id, archetype_id) is UNIQUE — reuse rather than mint, and only clean up what this
        # test actually created.
        binding = await self.conn.fetchval(
            "SELECT id FROM agent_bindings WHERE pack_id=$1 AND archetype_id=$2", pack, arch)
        if binding is None:
            binding = await self.conn.fetchval(
                "INSERT INTO agent_bindings (pack_id, archetype_id, persona_default, tool_grants, "
                "kpi_defs, tier_defaults) VALUES ($1,$2,'P','[]'::jsonb,'[]'::jsonb,'[]'::jsonb) "
                "RETURNING id", pack, arch)
            self.bindings.append(binding)
        await self.conn.execute(
            "INSERT INTO agent_instances (org_id, binding_id, persona_name, status, "
            "permission_manifest) VALUES ($1,$2,'P',$3,'{}'::jsonb)", org, binding, status)


@pytest.fixture()
async def world() -> AsyncIterator[World]:
    if not await _db_ready():
        pytest.skip("Postgres/billing not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    tag = f"ent-{uuid.uuid4().hex[:8]}"
    conn = await asyncpg.connect(_dsn())
    w = World(conn, tag)
    try:
        yield w
    finally:
        for oid in w.orgs:
            await conn.execute("DELETE FROM agent_instances WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM pack_installations WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM billing_subscriptions WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        for bid in w.bindings:
            await conn.execute("DELETE FROM agent_bindings WHERE id=$1", bid)
        await conn.execute("DELETE FROM billing_plans WHERE name LIKE $1", f"{tag}-plan-%")
        await conn.close()


async def _resolve(org: uuid.UUID, *, now: datetime | None = None):
    async with dbmod.get_sessionmaker()() as s:
        return await resolve(s, org, now=now)


def _reasons(eff, key: str) -> list[str]:
    return [e.reason for e in eff.excluded if e.key == key]


# ---- Subscription state -------------------------------------------------------------------------


async def test_a_store_that_never_subscribed_has_no_paid_capabilities(world: World) -> None:
    eff = await _resolve(await world.org())
    assert eff.capabilities == frozenset()
    assert eff.subscription_state == "none"


async def test_cancelled_history_is_distinguished_from_never_subscribed(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(features=["campaigns.whatsapp"]), "cancelled")
    eff = await _resolve(org)
    assert eff.subscription_state == "cancelled"
    assert eff.capabilities == frozenset()   # cancelled = immediately unentitled


async def test_a_retired_plan_still_serves_its_existing_active_subscriber(world: World) -> None:
    """`billing_plans.active` means 'eligible for new assignment', not 'revoke existing access'."""
    org = await world.org()
    await world.subscribe(org, await world.plan(features=["campaigns.whatsapp"], active=False))
    eff = await _resolve(org)
    assert eff.subscription_state == "active"
    assert "campaigns.whatsapp" in eff.capabilities


# ---- Legacy compatibility -----------------------------------------------------------------------


async def test_an_active_legacy_plan_with_no_features_gets_the_historical_baseline(
    world: World,
) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(features=[]))
    eff = await _resolve(org)
    assert eff.capabilities == LEGACY_BASELINE
    assert {g.source for g in eff.grants} == {"legacy_compat"}


async def test_a_legacy_campaigns_plan_keeps_baseline_and_campaigns(world: World) -> None:
    """The dependency validator would reject campaigns.whatsapp if `customers` were lost."""
    org = await world.org()
    await world.subscribe(org, await world.plan(features=["campaigns.whatsapp"]))
    eff = await _resolve(org)
    assert eff.capabilities == LEGACY_BASELINE | {"campaigns.whatsapp"}


async def test_the_legacy_campaign_dependency_survives_via_the_implied_channel(
    world: World,
) -> None:
    """Legacy plans have no `config.channels`; the whatsapp selection is reconstructed."""
    org = await world.org()
    await world.subscribe(org, await world.plan(features=["campaigns.whatsapp"]))
    eff = await _resolve(org)
    assert eff.channels == frozenset({"whatsapp"})
    assert "missing_channel_selection:channel.whatsapp" not in _reasons(eff, "campaigns.whatsapp")


async def test_legacy_grants_are_labelled_legacy_compat_never_plan(world: World) -> None:
    """PLAN-4 uses this to find plans still needing migration to the structured schema."""
    org = await world.org()
    await world.subscribe(org, await world.plan(features=["campaigns.whatsapp"]))
    eff = await _resolve(org)
    assert all(g.source == "legacy_compat" for g in eff.grants)


async def test_an_arbitrary_display_string_cannot_grant_a_capability(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(
        features=["Unlimited everything!", "seo", "ads.google", "wildcard"]))
    eff = await _resolve(org)
    assert eff.capabilities == LEGACY_BASELINE


# ---- Structured plans ---------------------------------------------------------------------------


async def test_structured_entitlements_override_legacy_features(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(
        features=["campaigns.whatsapp"],                       # must be ignored
        config={"entitlement_schema_version": 1, "entitlements": ["catalog"]}))
    eff = await _resolve(org)
    assert eff.capabilities == frozenset({"catalog"})
    assert [g.source for g in eff.grants] == ["plan"]


async def test_a_structured_plan_never_receives_the_legacy_baseline_or_channels(
    world: World,
) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(
        features=["campaigns.whatsapp"],
        config={"entitlement_schema_version": 1, "entitlements": ["catalog"]}))
    eff = await _resolve(org)
    assert not (LEGACY_BASELINE - {"catalog"}) & eff.capabilities
    assert eff.channels == frozenset()


async def test_a_structured_plan_missing_entitlements_fails_closed(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(
        features=["campaigns.whatsapp"],
        config={"entitlement_schema_version": 1, "agents": ["concierge"]}))
    eff = await _resolve(org)
    assert eff.capabilities == frozenset()
    assert "structured_plan_missing_entitlements" in _reasons(eff, "entitlements")


async def test_an_entitlements_typo_does_not_reactivate_legacy_features(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(
        features=["campaigns.whatsapp"],
        config={"entitlement_schema_version": 1, "entitlments": ["campaigns.whatsapp"]}))
    eff = await _resolve(org)
    assert eff.capabilities == frozenset()


async def test_an_unknown_schema_version_fails_closed(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(
        features=["campaigns.whatsapp"],
        config={"entitlement_schema_version": 99, "entitlements": ["catalog"]}))
    eff = await _resolve(org)
    assert eff.capabilities == frozenset()
    assert any("unknown_entitlement_schema_version" in e.reason for e in eff.excluded)


async def test_a_structured_empty_entitlements_list_grants_nothing(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(
        features=["campaigns.whatsapp"],
        config={"entitlement_schema_version": 1, "entitlements": []}))
    eff = await _resolve(org)
    assert eff.capabilities == frozenset()


# ---- Capability filtering -----------------------------------------------------------------------


async def test_planned_and_partial_keys_grant_nothing_with_named_reasons(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(config={
        "entitlement_schema_version": 1,
        "entitlements": ["seo", "ads.google", "pricing", "nonsense", "catalog"]}))
    eff = await _resolve(org)
    assert eff.capabilities == frozenset({"catalog"})
    assert _reasons(eff, "seo") == ["not_grantable:planned"]
    assert _reasons(eff, "nonsense") == ["not_in_catalog"]
    assert _reasons(eff, "pricing") == ["governed_by:rbac:catalog:read"]


async def test_a_legacy_alias_resolves_then_is_refused_on_its_merits(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(config={
        "entitlement_schema_version": 1, "entitlements": ["ads.instagram"]}))
    eff = await _resolve(org)
    assert eff.capabilities == frozenset()
    assert _reasons(eff, "social.instagram_publishing") == ["governed_by:not_wired"] or any(
        "not_grantable" in r or "governed_by" in r
        for r in _reasons(eff, "social.instagram_publishing"))


# ---- Dependencies -------------------------------------------------------------------------------


async def test_campaigns_needs_the_channel_selected(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(config={
        "entitlement_schema_version": 1,
        "entitlements": ["campaigns.whatsapp", "customers"], "channels": ["whatsapp"]}))
    eff = await _resolve(org)
    assert "campaigns.whatsapp" in eff.capabilities


async def test_campaigns_fails_closed_without_a_channel_selection(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(config={
        "entitlement_schema_version": 1,
        "entitlements": ["campaigns.whatsapp", "customers"]}))
    eff = await _resolve(org)
    assert "campaigns.whatsapp" not in eff.capabilities
    assert "missing_channel_selection:channel.whatsapp" in _reasons(eff, "campaigns.whatsapp")


async def test_a_missing_capability_dependency_fails_closed_and_is_not_auto_granted(
    world: World,
) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(config={
        "entitlement_schema_version": 1,
        "entitlements": ["campaigns.whatsapp"], "channels": ["whatsapp"]}))
    eff = await _resolve(org)
    assert eff.capabilities == frozenset()          # customers was NOT auto-granted
    assert "missing_dependency:customers" in _reasons(eff, "campaigns.whatsapp")


async def test_a_dropped_dependency_cascades_deterministically(world: World) -> None:
    """campaigns.analytics → campaigns.whatsapp → customers: losing the root drops the chain."""
    org = await world.org()
    await world.subscribe(org, await world.plan(config={
        "entitlement_schema_version": 1,
        "entitlements": ["campaigns.analytics", "campaigns.whatsapp"], "channels": ["whatsapp"]}))
    eff = await _resolve(org)
    assert eff.capabilities == frozenset()


# ---- Vertical pack filtering --------------------------------------------------------------------


async def test_a_vertical_capability_is_excluded_without_the_pack(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(config={
        "entitlement_schema_version": 1, "entitlements": ["jewelry.rate_operations"]}))
    eff = await _resolve(org)
    assert eff.capabilities == frozenset()
    assert _reasons(eff, "jewelry.rate_operations") == ["pack_not_installed:jewelry"]


async def test_a_vertical_capability_resolves_with_the_pack_installed(world: World) -> None:
    org = await world.org()
    await world.install_pack(org, "jewelry", "active")
    await world.subscribe(org, await world.plan(config={
        "entitlement_schema_version": 1, "entitlements": ["jewelry.rate_operations"]}))
    eff = await _resolve(org)
    assert "jewelry.rate_operations" in eff.capabilities


@pytest.mark.parametrize("status", ["installing", "paused", "failed", "uninstalled"])
async def test_a_non_active_pack_install_does_not_entitle(world: World, status: str) -> None:
    org = await world.org()
    await world.install_pack(org, "jewelry", status)
    await world.subscribe(org, await world.plan(config={
        "entitlement_schema_version": 1, "entitlements": ["jewelry.rate_operations"]}))
    eff = await _resolve(org)
    assert eff.capabilities == frozenset()


# ---- Agents -------------------------------------------------------------------------------------


async def test_an_archetype_without_a_tenant_binding_is_excluded(world: World) -> None:
    """Globally valid ≠ supported by this tenant's installed vertical pack."""
    org = await world.org()
    await world.subscribe(org, await world.plan(config={
        "entitlement_schema_version": 1, "entitlements": [], "agents": ["concierge"]}))
    eff = await _resolve(org)
    assert eff.agents == frozenset()
    assert _reasons(eff, "concierge") == ["no_tenant_binding"]


async def test_a_bound_archetype_is_commercially_effective(world: World) -> None:
    org = await world.org()
    await world.bind_agent(org, "concierge", "active")
    await world.subscribe(org, await world.plan(config={
        "entitlement_schema_version": 1, "entitlements": [], "agents": ["concierge"]}))
    eff = await _resolve(org)
    assert eff.agents == frozenset({"concierge"})


@pytest.mark.parametrize("status", ["paused", "shadow", "circuit_open"])
async def test_operational_status_is_not_entitlement_truth(world: World, status: str) -> None:
    """A paused instance is an operational state, not a commercial one."""
    org = await world.org()
    await world.bind_agent(org, "concierge", status)
    await world.subscribe(org, await world.plan(config={
        "entitlement_schema_version": 1, "entitlements": [], "agents": ["concierge"]}))
    eff = await _resolve(org)
    assert eff.agents == frozenset({"concierge"})


async def test_an_invalid_archetype_is_excluded(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(config={
        "entitlement_schema_version": 1, "entitlements": [], "agents": ["wizard"]}))
    eff = await _resolve(org)
    assert eff.agents == frozenset()
    assert _reasons(eff, "wizard") == ["unknown_archetype"]


async def test_agents_never_leak_into_capabilities(world: World) -> None:
    org = await world.org()
    await world.bind_agent(org, "concierge", "active")
    await world.subscribe(org, await world.plan(config={
        "entitlement_schema_version": 1, "entitlements": ["catalog"], "agents": ["concierge"]}))
    eff = await _resolve(org)
    assert eff.capabilities == frozenset({"catalog"})
    assert "agent.concierge" not in eff.capabilities


# ---- Channels -----------------------------------------------------------------------------------


async def test_a_channel_selection_implies_nothing_about_connection(world: World) -> None:
    """No `channels` row exists for this org — selection is a commercial choice only."""
    org = await world.org()
    await world.subscribe(org, await world.plan(config={
        "entitlement_schema_version": 1, "entitlements": [], "channels": ["whatsapp"]}))
    eff = await _resolve(org)
    assert eff.channels == frozenset({"whatsapp"})


async def test_an_unknown_channel_type_is_excluded(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(config={
        "entitlement_schema_version": 1, "entitlements": [], "channels": ["carrier_pigeon"]}))
    eff = await _resolve(org)
    assert eff.channels == frozenset()
    assert _reasons(eff, "carrier_pigeon") == ["unknown_channel_type"]


# ---- Promotions ---------------------------------------------------------------------------------


def _promo_plan(**over) -> dict:
    promo = {"capability_key": "landing_pages", "label": "Launch offer",
             "starts_at": T0.isoformat(), "ends_at": T1.isoformat(), **over}
    return {"entitlement_schema_version": 1, "entitlements": ["catalog"], "promotions": [promo]}


@pytest.mark.parametrize("when,granted", [
    (T0 - timedelta(microseconds=1), False),   # before
    (T0, True),                                # exact start — inclusive
    (T1 - timedelta(microseconds=1), True),    # last instant
    (T1, False),                               # exact end — exclusive
    (T1 + timedelta(days=1), False),           # expired, still stored
])
async def test_promotion_window_boundaries(world: World, when: datetime, granted: bool) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(config=_promo_plan()))
    eff = await _resolve(org, now=when)
    assert ("landing_pages" in eff.capabilities) is granted


async def test_an_active_promotion_carries_its_provenance(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(config=_promo_plan()))
    eff = await _resolve(org, now=T0)
    grant = next(g for g in eff.grants if g.key == "landing_pages")
    assert grant.source == "promotion"
    assert grant.promotion_label == "Launch offer"
    assert grant.ends_at == T1


async def test_a_disabled_promotion_grants_nothing(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(config=_promo_plan(enabled=False)))
    eff = await _resolve(org, now=T0)
    assert "landing_pages" not in eff.capabilities


async def test_a_promotion_still_passes_every_normal_filter(world: World) -> None:
    """A promotion may add a candidate but never bypasses catalog, grantable, pack or dependency
    rules."""
    org = await world.org()
    await world.subscribe(org, await world.plan(config=_promo_plan(capability_key="seo")))
    eff = await _resolve(org, now=T0)
    assert "seo" not in eff.capabilities
    assert _reasons(eff, "seo") == ["not_grantable:planned"]


async def test_a_promoted_vertical_capability_still_needs_the_pack(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(
        config=_promo_plan(capability_key="jewelry.rate_operations")))
    eff = await _resolve(org, now=T0)
    assert "jewelry.rate_operations" not in eff.capabilities


async def test_one_malformed_promotion_does_not_deny_the_tenant(world: World) -> None:
    cfg = _promo_plan()
    cfg["promotions"].append({"capability_key": "campaigns.whatsapp"})   # missing starts_at
    org = await world.org()
    await world.subscribe(org, await world.plan(config=cfg))
    eff = await _resolve(org, now=T0)
    assert "catalog" in eff.capabilities and "landing_pages" in eff.capabilities
    assert any(e.component == "promotion" for e in eff.excluded)


# ---- Limits / addons ----------------------------------------------------------------------------


async def test_limits_are_reported_without_creating_a_second_seat_mechanism(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(staff=7, config={
        "entitlement_schema_version": 1, "entitlements": []}))
    eff = await _resolve(org)
    assert eff.limits.max_staff == 7 and eff.limits.max_managers == 2


async def test_addons_are_display_metadata_only(world: World) -> None:
    org = await world.org()
    await world.subscribe(org, await world.plan(config={
        "entitlement_schema_version": 1, "entitlements": [], "addons": ["priority_support"]}))
    eff = await _resolve(org)
    assert eff.addons == ("priority_support",)
    assert "priority_support" not in eff.capabilities
