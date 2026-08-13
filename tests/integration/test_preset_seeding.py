"""Canonical preset seeding, immutability and edit protection (PLAN-3) against real Postgres.

Every row these tests reason about is created here, so nothing depends on whatever the local
database happens to contain. Skips when the DB is unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.billing import service
from core.billing.presets import (
    GENERIC_PRESETS,
    InsufficientVisibility,
    Preset,
    apply_presets,
    assert_global_visibility,
)
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.entitlements import resolve


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.billing_plans')"))
    finally:
        await conn.close()


class Bench:
    """A self-contained world: every plan/org it touches is tagged and torn down."""

    def __init__(self, conn: asyncpg.Connection, tag: str) -> None:
        self.conn, self.tag = conn, tag
        self.orgs: list[uuid.UUID] = []
        self.plans: list[uuid.UUID] = []
        self.bindings: list[uuid.UUID] = []

    def presets(self) -> tuple[Preset, ...]:
        """The canonical presets, renamed/re-keyed under this test's tag so parallel state and the
        real seeded rows can never collide."""
        from core.billing.presets import all_presets

        return tuple(
            Preset(**{**p.__dict__,
                      "preset_key": f"{self.tag}.{p.preset_key}",
                      "name": f"{self.tag} {p.name}"})
            for p in all_presets())

    async def org(self, vertical: str = "jewelry") -> uuid.UUID:
        oid = uuid.uuid4()
        await self.conn.execute(
            "INSERT INTO organizations (id, name, vertical) VALUES ($1,$2,$3)",
            oid, f"{self.tag}-{len(self.orgs)}", vertical)
        self.orgs.append(oid)
        return oid

    async def raw_plan(self, name: str, *, config: dict, features: list[str] | None = None,
                       ) -> uuid.UUID:
        pid = uuid.uuid4()
        await self.conn.execute(
            "INSERT INTO billing_plans (id, name, price_minor, features, config, max_managers, "
            "max_staff) VALUES ($1,$2,1000,$3::jsonb,$4::jsonb,1,1)",
            pid, f"{self.tag}-{name}", json.dumps(features or []), json.dumps(config))
        self.plans.append(pid)
        return pid

    async def plan_id(self, preset_key: str) -> uuid.UUID:
        pid = await self.conn.fetchval(
            "SELECT id FROM billing_plans WHERE config->>'preset_key' = $1", preset_key)
        if pid is not None and pid not in self.plans:
            self.plans.append(pid)
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

    async def bind_agent(self, org: uuid.UUID, slug: str) -> None:
        arch = await self.conn.fetchval("SELECT id FROM agent_archetypes WHERE slug=$1", slug)
        # Bind to a named pack, never `LIMIT 1`: other suites create packs too, and an
        # arbitrary pick made this fixture order-dependent.
        pack = await self.conn.fetchval("SELECT id FROM packs WHERE slug='jewelry'")
        # (pack_id, archetype_id) is UNIQUE — reuse an existing binding rather than minting one,
        # and only clean up bindings this test actually created.
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
            "permission_manifest) VALUES ($1,$2,'P','active','{}'::jsonb)", org, binding)

    async def row(self, plan_id: uuid.UUID) -> dict:
        r = await self.conn.fetchrow(
            "SELECT name, price_minor, active, description, features, max_managers, max_staff, "
            "config FROM billing_plans WHERE id=$1", plan_id)
        return dict(r)


@pytest.fixture()
async def bench() -> AsyncIterator[Bench]:
    if not await _db_ready():
        pytest.skip("Postgres/billing not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    tag = f"p3-{uuid.uuid4().hex[:8]}"
    conn = await asyncpg.connect(_dsn())
    b = Bench(conn, tag)
    try:
        yield b
    finally:
        for oid in b.orgs:
            await conn.execute("DELETE FROM agent_instances WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM pack_installations WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM billing_subscriptions WHERE org_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        for bid in b.bindings:
            await conn.execute("DELETE FROM agent_bindings WHERE id=$1", bid)
        await conn.execute("DELETE FROM billing_plans WHERE name LIKE $1", f"{tag}%")
        await conn.close()
        await dbmod.get_engine().dispose()
        dbmod.get_engine.cache_clear()
        dbmod.get_sessionmaker.cache_clear()


async def _seed(bench: Bench, **kw) -> list:
    """Run the seeder on a privileged connection, as production would."""
    engine = create_async_engine(get_settings().database_migrator_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            out = await apply_presets(s, presets=bench.presets(), **kw)
            await s.commit()
        for p in bench.presets():
            await bench.plan_id(p.preset_key)
        return out
    finally:
        await engine.dispose()


def _by_key(outcomes: list, key_suffix: str, tag: str):
    return next(o for o in outcomes if o.preset_key == f"{tag}.{key_suffix}")


# ---- Seeding + idempotency ----------------------------------------------------------------------


async def test_seeding_creates_every_preset_then_is_a_no_op(bench: Bench) -> None:
    first = await _seed(bench)
    assert {o.action for o in first} == {"created"}
    assert len(first) == 4

    second = await _seed(bench)
    assert {o.action for o in second} == {"unchanged"}, [o.detail for o in second]

    third = await _seed(bench)
    assert {o.action for o in third} == {"unchanged"}

    names = await bench.conn.fetch(
        "SELECT name, count(*) FROM billing_plans WHERE name LIKE $1 GROUP BY name",
        f"{bench.tag}%")
    assert all(r["count"] == 1 for r in names), "re-running duplicated a plan"


async def test_seeded_rows_carry_the_structured_schema_and_empty_features(bench: Bench) -> None:
    await _seed(bench)
    for preset in bench.presets():
        row = await bench.row(await bench.plan_id(preset.preset_key))
        cfg = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
        feats = json.loads(row["features"]) if isinstance(row["features"], str) else row["features"]
        assert cfg["entitlement_schema_version"] == 1
        assert cfg["preset_key"] == preset.preset_key
        assert feats == [], "features must stay empty — it is display/compat data only"


async def test_the_seeder_refuses_without_global_subscription_visibility(bench: Bench) -> None:
    """Under an RLS-bound role the sold check would report every plan as never-sold."""
    engine = create_async_engine(get_settings().database_url)  # app_rw
    try:
        async with async_sessionmaker(engine)() as s:
            with pytest.raises(InsufficientVisibility):
                await assert_global_visibility(s)
            with pytest.raises(InsufficientVisibility):
                await apply_presets(s, presets=bench.presets(), dry_run=True)
    finally:
        await engine.dispose()


async def test_a_dry_run_writes_nothing(bench: Bench) -> None:
    out = await _seed(bench, dry_run=True)
    assert {o.action for o in out} == {"created"}
    assert await bench.conn.fetchval(
        "SELECT count(*) FROM billing_plans WHERE name LIKE $1", f"{bench.tag}%") == 0


# ---- Immutability once sold ---------------------------------------------------------------------


async def _bump(bench: Bench) -> tuple:
    """Presets at a higher version, as a future price change would look."""
    return tuple(
        Preset(**{**p.__dict__, "price_minor": p.price_minor + 100_000}) for p in bench.presets())


async def test_an_unsold_preset_updates_when_the_definition_changes(bench: Bench) -> None:
    await _seed(bench)
    pid = await bench.plan_id(f"{bench.tag}.recover")
    before = (await bench.row(pid))["price_minor"]

    import core.billing.presets as pmod

    engine = create_async_engine(get_settings().database_migrator_url)
    try:
        original = pmod.PRESET_VERSION
        pmod.PRESET_VERSION = original + 1  # a later definition
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            out = await apply_presets(s, presets=await _bump(bench))
            await s.commit()
        assert _by_key(out, "recover", bench.tag).action == "updated"
        assert (await bench.row(pid))["price_minor"] == before + 100_000
    finally:
        pmod.PRESET_VERSION = original
        await engine.dispose()


@pytest.mark.parametrize("sub_status", ["active", "cancelled"])
async def test_a_sold_preset_is_never_mutated(bench: Bench, sub_status: str) -> None:
    """Cancelled history counts: what a past subscriber bought stays exactly as sold."""
    await _seed(bench)
    pid = await bench.plan_id(f"{bench.tag}.recover")
    org = await bench.org()
    await bench.subscribe(org, pid, sub_status)
    before = await bench.row(pid)

    import core.billing.presets as pmod

    engine = create_async_engine(get_settings().database_migrator_url)
    try:
        original = pmod.PRESET_VERSION
        pmod.PRESET_VERSION = original + 1
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            out = await apply_presets(s, presets=await _bump(bench))
            await s.commit()
    finally:
        pmod.PRESET_VERSION = original
        await engine.dispose()

    assert _by_key(out, "recover", bench.tag).action == "skipped_sold"
    assert await bench.row(pid) == before, "a sold commercial snapshot was mutated"
    # unsold siblings still move, so the skip is targeted rather than a blanket abort
    assert _by_key(out, "grow", bench.tag).action == "updated"


async def test_operator_drift_is_reported_not_clobbered(bench: Bench) -> None:
    await _seed(bench)
    pid = await bench.plan_id(f"{bench.tag}.grow")
    await bench.conn.execute(
        "UPDATE billing_plans SET description = 'operator edited' WHERE id=$1", pid)

    out = await _seed(bench)
    assert _by_key(out, "grow", bench.tag).action == "skipped_drift"
    assert (await bench.row(pid))["description"] == "operator edited"


# ---- Non-preset rows are untouchable ------------------------------------------------------------


async def test_legacy_and_custom_rows_are_never_touched_by_seeding(bench: Bench) -> None:
    legacy = await bench.raw_plan("legacy", config={}, features=["campaigns.whatsapp"])
    custom = await bench.raw_plan(
        "custom", config={"entitlement_schema_version": 1, "entitlements": ["catalog"]})
    before = {"legacy": await bench.row(legacy), "custom": await bench.row(custom)}

    await _seed(bench)
    await _seed(bench)

    assert await bench.row(legacy) == before["legacy"]
    assert await bench.row(custom) == before["custom"]


# ---- Canonical edit protection ------------------------------------------------------------------


async def test_editing_a_canonical_preset_through_the_legacy_editor_is_refused(
    bench: Bench,
) -> None:
    """The CP-1 payload rebuilds config from agents/channels/addons only — exactly the shape that
    would strip the structured contract."""
    await _seed(bench)
    pid = await bench.plan_id(f"{bench.tag}.scale")
    before = await bench.row(pid)

    async with dbmod.get_sessionmaker()() as s:
        with pytest.raises(service.CanonicalPresetLocked):
            await service.update_plan(
                s, pid, name="Scale", price_minor=1, active=False, description="hijacked",
                features=[], max_managers=99, max_staff=99,
                config={"agents": [], "channels": [], "addons": []})
        await s.rollback()

    after = await bench.row(pid)
    assert after == before
    cfg = json.loads(after["config"]) if isinstance(after["config"], str) else after["config"]
    assert cfg["entitlement_schema_version"] == 1      # not stripped
    assert cfg["entitlements"]                          # not stripped
    assert cfg["preset_key"] and cfg["preset_version"]  # identity intact


async def test_a_non_preset_plan_remains_editable(bench: Bench) -> None:
    pid = await bench.raw_plan("editable", config={"agents": ["concierge"]})
    async with dbmod.get_sessionmaker()() as s:
        updated = await service.update_plan(
            s, pid, name=f"{bench.tag}-renamed", price_minor=4242, active=True,
            description="fine", features=[], max_managers=1, max_staff=1,
            config={"agents": [], "channels": [], "addons": []})
        await s.commit()
    assert updated is not None and updated["price_minor"] == 4242


async def test_create_plan_refuses_caller_supplied_preset_identity(bench: Bench) -> None:
    async with dbmod.get_sessionmaker()() as s:
        with pytest.raises(service.CanonicalPresetLocked):
            await service.create_plan(
                s, name=f"{bench.tag}-forged", price_minor=1, description=None, features=[],
                config={"preset_key": "recover", "preset_version": 1})
        await s.rollback()


# ---- Resolution through PLAN-2 ------------------------------------------------------------------


async def _resolve_on(bench: Bench, preset_key: str, *, pack: str | None = "jewelry"):
    org = await bench.org()
    if pack:
        await bench.install_pack(org, pack)
    await bench.bind_agent(org, "concierge")
    await bench.subscribe(org, await bench.plan_id(preset_key))
    async with dbmod.get_sessionmaker()() as s:
        return await resolve(s, org)


@pytest.mark.parametrize("tier,expected", [
    ("recover", {"conversations", "catalog", "customers", "ghost_recovery"}),
    ("grow", {"conversations", "catalog", "customers", "ghost_recovery",
              "campaigns.whatsapp", "campaigns.analytics", "landing_pages"}),
    ("scale", {"conversations", "catalog", "customers", "ghost_recovery",
               "campaigns.whatsapp", "campaigns.analytics", "landing_pages",
               "catalog.ingestion"}),
])
async def test_each_preset_resolves_to_its_exact_capability_set(
    bench: Bench, tier: str, expected: set[str]
) -> None:
    await _seed(bench)
    eff = await _resolve_on(bench, f"{bench.tag}.{tier}")
    assert eff.capabilities == expected
    assert eff.agents == frozenset({"concierge"})
    assert eff.channels == frozenset({"whatsapp"})
    assert eff.subscription_state == "active"


async def test_presets_resolve_as_plan_grants_never_legacy_compat(bench: Bench) -> None:
    await _seed(bench)
    for tier in ("recover", "grow", "scale"):
        eff = await _resolve_on(bench, f"{bench.tag}.{tier}")
        assert {g.source for g in eff.grants} == {"plan"}, tier


async def test_seat_limits_reach_the_resolver(bench: Bench) -> None:
    await _seed(bench)
    for tier, mm, ms in (("recover", 0, 2), ("grow", 1, 4), ("scale", 2, 8)):
        eff = await _resolve_on(bench, f"{bench.tag}.{tier}")
        assert (eff.limits.max_managers, eff.limits.max_staff) == (mm, ms), tier


async def test_the_jewelry_variant_resolves_its_capability_only_with_the_pack(
    bench: Bench,
) -> None:
    await _seed(bench)
    with_pack = await _resolve_on(bench, f"{bench.tag}.scale.jewelry", pack="jewelry")
    assert "jewelry.rate_operations" in with_pack.capabilities

    without = await _resolve_on(bench, f"{bench.tag}.scale.jewelry", pack=None)
    assert "jewelry.rate_operations" not in without.capabilities
    assert any(e.reason == "pack_not_installed:jewelry" for e in without.excluded)
    # the generic Scale content still resolves for that tenant
    assert "catalog.ingestion" in without.capabilities


async def test_the_generic_scale_preset_grants_no_vertical_capability(bench: Bench) -> None:
    await _seed(bench)
    eff = await _resolve_on(bench, f"{bench.tag}.scale", pack="jewelry")
    assert "jewelry.rate_operations" not in eff.capabilities


async def test_a_display_bullet_can_never_authorize(bench: Bench) -> None:
    await _seed(bench)
    eff = await _resolve_on(bench, f"{bench.tag}.grow")
    bullets = set(GENERIC_PRESETS[1].display_bullets)
    assert bullets.isdisjoint(eff.capabilities)
