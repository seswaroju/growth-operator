"""Approval policy engine over real rules (MVP-065) — Postgres under app_rw.

Covers the ap-* semantics: core tier-4 minimums, pack defaults, CEL-guarded tiers, max-tier-wins,
tighten-only tenant validation, self-expiring incident tightening, and the unknown-action
fail-safe. (The exact ap-01..05/13..15 fixture suite lives in the vault and is illustrative; these
assert the documented behaviour, same convention as the pricing goldens.) Skips when DB unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.approvals import engine
from core.approvals.engine import ActionContext
from core.common import db as dbmod
from core.common.config import get_settings
from core.common.errors import GrowthOperatorError
from core.tenancy.middleware import org_scoped_session


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.approval_policies')"))
    finally:
        await conn.close()


class Scene:
    def __init__(self, org: uuid.UUID, pack_id: uuid.UUID) -> None:
        self.org = org
        self.pack_id = pack_id

    async def policy(
        self, scope: str, action_type: str, tier: int, *, cel: str | None = None,
        tenant: bool = False, chain: list | None = None, timeout: int | None = None,
    ) -> None:
        import json
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute(
                "INSERT INTO approval_policies (scope, org_id, pack_id, action_type, tier, "
                " cel_expr, description, approver_chain, timeout_s) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9)",
                scope, self.org if tenant else None,
                self.pack_id if scope == "pack" else None,
                action_type, tier, cel, f"{scope} {action_type} t{tier}",
                json.dumps(chain or []), timeout,
            )
        finally:
            await conn.close()

    async def incident(self, action_type: str, tier: int, *, hours: int) -> None:
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute(
                "INSERT INTO incident_tightening (org_id, action_type, tightened_to_tier, "
                " expires_at) VALUES ($1,$2,$3, now() + make_interval(hours => $4))",
                self.org, action_type, tier, hours,
            )
        finally:
            await conn.close()


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/approvals (014) not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'P')", org)
        pack_id = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id",
            f"p{org.hex[:8]}",
        )
    finally:
        await conn.close()
    yield Scene(org, pack_id)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM approval_policies WHERE org_id=$1 OR pack_id=$2",
                           org, pack_id)
        await conn.execute("DELETE FROM incident_tightening WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack_id)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _tier(scene: Scene, action: str, **ctx: object) -> tuple[int, list[str]]:
    async with org_scoped_session(scene.org) as s:
        d = await engine.evaluate(s, ActionContext(org_id=scene.org, action_type=action, **ctx))
    return d.tier, d.matched_rules


async def test_ap01_core_tier4_never_autonomous(scene: Scene) -> None:
    tier, matched = await _tier(scene, "payment.charge")
    assert tier == 4 and "core:tier4" in matched


async def test_ap02_pack_default_tier(scene: Scene) -> None:
    await scene.policy("pack", "messages.send", 2)  # always-match default
    tier, _ = await _tier(scene, "messages.send")
    assert tier == 2


async def test_ap03_cel_guarded_tier(scene: Scene) -> None:
    await scene.policy("pack", "discount.apply", 1)  # base
    await scene.policy("pack", "discount.apply", 3, cel="amount_minor > 50000")  # big discounts
    high, _ = await _tier(scene, "discount.apply", amount_minor=60000)
    low, _ = await _tier(scene, "discount.apply", amount_minor=40000)
    assert high == 3  # guard matched → max(1,3)
    assert low == 1   # guard didn't match → only the base rule


async def test_ap04_max_tier_wins(scene: Scene) -> None:
    await scene.policy("pack", "campaign.send", 1)
    await scene.policy("pack", "campaign.send", 3)
    tier, matched = await _tier(scene, "campaign.send")
    assert tier == 3 and len(matched) == 2  # both matched, max wins


async def test_ap05_tighten_only_validator(scene: Scene) -> None:
    await scene.policy("pack", "messages.send", 2)  # baseline tier 2
    async with org_scoped_session(scene.org) as s:
        await engine.validate_tenant_rule(s, "messages.send", 3)  # tighten — OK
        with pytest.raises(GrowthOperatorError) as exc:
            await engine.validate_tenant_rule(s, "messages.send", 1)  # loosen — rejected
    assert exc.value.code == "config_schema_violation"


async def test_ap13_incident_tightening_active_then_expired(scene: Scene) -> None:
    await scene.policy("pack", "refund.issue", 0)  # normally autonomous
    await scene.incident("refund.issue", 2, hours=24)  # active tightening
    tier, matched = await _tier(scene, "refund.issue")
    assert tier == 2 and "incident" in matched

    # An expired incident must not count.
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "UPDATE incident_tightening SET expires_at = now() - interval '1 hour' "
            "WHERE org_id=$1", scene.org,
        )
    finally:
        await conn.close()
    tier2, _ = await _tier(scene, "refund.issue")
    assert tier2 == 0  # back to the base pack rule


async def test_ap15_unknown_action_fails_safe(scene: Scene) -> None:
    tier, matched = await _tier(scene, "some.unregistered.action")
    assert tier == engine.DEFAULT_UNKNOWN_TIER and matched == []


async def test_tenant_rule_tightens_over_pack(scene: Scene) -> None:
    await scene.policy("pack", "messages.send", 2)
    await scene.policy("tenant", "messages.send", 3, tenant=True)  # this org tightened
    tier, _ = await _tier(scene, "messages.send")
    assert tier == 3
