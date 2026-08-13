"""Mediation proxy check chain (MVP-060) against real Postgres (for the audit chain).

Proves the acceptance: an out-of-manifest call is denied + audited + alerted, and ≥3 manifest
violations abort the run. Also covers the ordered chain — integrity → grant → narrowing → params
→ rate → budget → tier → audit → execute — each failing with the right structured tool error.
Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.mediation import manifest as manifest_mod
from core.mediation import proxy
from core.mediation.proxy import RunAborted, RunContext, ToolResult
from core.tenancy.middleware import org_scoped_session


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.audit_log')"))
    finally:
        await conn.close()


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}
        self.streams: list[tuple[str, dict[str, Any]]] = []
        self.zsets: dict[str, dict[str, float]] = {}

    async def incr(self, key: str) -> int:
        self.kv[key] = int(self.kv.get(key, 0)) + 1
        return self.kv[key]

    async def incrby(self, key: str, amount: int) -> int:
        self.kv[key] = int(self.kv.get(key, 0)) + amount
        return self.kv[key]

    async def expire(self, key: str, secs: int) -> bool:
        return True

    async def get(self, key: str) -> Any:
        return self.kv.get(key)

    async def set(self, key: str, value: Any, **kw: Any) -> bool:
        self.kv[key] = value
        return True

    async def delete(self, key: str) -> int:
        return int(self.kv.pop(key, None) is not None)

    async def xadd(self, stream: str, fields: dict[str, Any]) -> str:
        self.streams.append((stream, fields))
        return "1-1"

    async def zremrangebyscore(self, key: str, mn: float, mx: float) -> int:
        z = self.zsets.get(key, {})
        stale = [m for m, s in z.items() if mn <= s <= mx]
        for m in stale:
            del z[m]
        return len(stale)

    async def zcard(self, key: str) -> int:
        return len(self.zsets.get(key, {}))

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)


def _manifest(tools: list[dict], *, budgets: dict | None = None) -> tuple[dict, str]:
    body = {
        "manifest_version": 3, "tools": tools,
        "budgets": budgets or {"sends_day": 300},
        "untrusted_narrowing": {"allow": ["catalog.search"]},
    }
    m = manifest_mod.sign(body)  # the proxy verifies the ed25519 signature (MVP-061)
    return m, manifest_mod.manifest_hash(m)


#: PLAN-5 gates every tool call on the run's agent being commercially entitled, so the fixture
#: provisions a real entitled store rather than a bare org — the realistic path these tests mean
#: to exercise. A fake instance id now (correctly) fails closed.
ENTITLED = ["conversations", "catalog", "customers", "landing_pages", "campaigns.whatsapp"]

_INSTANCE: dict[str, uuid.UUID] = {}


@pytest.fixture()
async def org() -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres/audit not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org_id, plan_id = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    binding = None
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, vertical) VALUES ($1,'M','jewelry')", org_id)
        import json as _json
        await conn.execute(
            "INSERT INTO billing_plans (id, name, price_minor, features, config) "
            "VALUES ($1,$2,1,'[]'::jsonb,$3::jsonb)",
            plan_id, f"med-{org_id.hex[:8]}",
            _json.dumps({"entitlement_schema_version": 1, "entitlements": ENTITLED,
                         "agents": ["concierge"], "channels": ["whatsapp"]}))
        await conn.execute(
            "INSERT INTO billing_subscriptions (org_id, plan_id, status) VALUES ($1,$2,'active')",
            org_id, plan_id)
        pack = await conn.fetchval("SELECT id FROM packs WHERE slug='jewelry'")
        if pack is None:
            pack = uuid.uuid4()
            await conn.execute(
                "INSERT INTO packs (id, slug, version, platform_api, manifest, bundle_uri, "
                "signature, status) VALUES ($1,'jewelry','1','1','{}'::jsonb,'x','x','published')",
                pack)
        await conn.execute(
            "INSERT INTO pack_installations (org_id, pack_id, status) VALUES ($1,$2,'active')",
            org_id, pack)
        arch = await conn.fetchval("SELECT id FROM agent_archetypes WHERE slug='concierge'")
        binding = await conn.fetchval(
            "SELECT id FROM agent_bindings WHERE pack_id=$1 AND archetype_id=$2", pack, arch)
        if binding is None:
            binding = await conn.fetchval(
                "INSERT INTO agent_bindings (pack_id, archetype_id, persona_default, tool_grants, "
                "kpi_defs, tier_defaults) VALUES ($1,$2,'P','[]'::jsonb,'[]'::jsonb,'[]'::jsonb) "
                "RETURNING id", pack, arch)
        _INSTANCE[str(org_id)] = await conn.fetchval(
            "INSERT INTO agent_instances (org_id, binding_id, persona_name, status, "
            "permission_manifest) VALUES ($1,$2,'P','active','{}'::jsonb) RETURNING id",
            org_id, binding)
    finally:
        await conn.close()
    yield org_id
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", org_id)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM agent_instances WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM pack_installations WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM billing_subscriptions WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org_id)
        await conn.execute("DELETE FROM billing_plans WHERE id=$1", plan_id)
        _INSTANCE.pop(str(org_id), None)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _ctx(org_id: uuid.UUID, manifest: dict, digest: str, *, untrusted: bool = False) -> RunContext:
    return RunContext(
        org_id=org_id, run_id=uuid.uuid4(), instance_id=_INSTANCE[str(org_id)],
        manifest=manifest, manifest_hash=digest, untrusted=untrusted,
    )


async def _audit_actions(org_id: uuid.UUID) -> list[str]:
    conn = await asyncpg.connect(_dsn())
    try:
        return [r["action"] for r in await conn.fetch(
            "SELECT action FROM audit_log WHERE org_id=$1 ORDER BY seq", org_id)]
    finally:
        await conn.close()


async def test_out_of_manifest_denied_audited_alerted(org: uuid.UUID) -> None:
    m, h = _manifest([{"name": "catalog.search", "read_only": True}])
    ctx = _ctx(org, m, h)
    redis = FakeRedis()
    async with org_scoped_session(org) as s:
        result = await proxy.call(ctx, "messages.send", {}, session=s, redis=redis, registry={})
        await s.commit()
    assert isinstance(result, ToolResult) and not result.ok
    assert result.error is not None and result.error.code == "permission_denied_manifest"
    assert "tool.messages.send:denied" in await _audit_actions(org)  # audited
    assert any(st == "gop:events:alert.ops.v1" for st, _ in redis.streams)  # alerted


async def test_untrusted_content_narrows_subsequent_tools(org: uuid.UUID) -> None:
    # web_fetch produces external content -> the run narrows to the manifest's allow-list until a
    # human boundary. messages.send is denied; catalog.search (allow-listed) still works (MVP-062).
    m, h = _manifest([{"name": "web_fetch", "read_only": True},
                      {"name": "messages.send", "requires_tier_eval": True},
                      {"name": "catalog.search", "read_only": True}])
    ctx = _ctx(org, m, h)
    redis = FakeRedis()

    async def web(c: RunContext, p: dict, s: Any, aid: uuid.UUID) -> Any:
        return {"body": "some fetched web page"}

    async def search(c: RunContext, p: dict, s: Any, aid: uuid.UUID) -> Any:
        return {"results": []}

    reg = {"web_fetch": web, "catalog.search": search}
    # PLAN-5 fails closed on an unclassified tool, so this fictional one declares itself exactly
    # as a real tool must. Removing this line makes the proxy refuse it — which is the point.
    from core.mediation.tools import TOOL_PLAN_EXEMPT

    TOOL_PLAN_EXEMPT["web_fetch"] = "test-only fixture tool; reads external content, no plan grant"
    try:
        async with org_scoped_session(org) as s:
            fetched = await proxy.call(
                ctx, "web_fetch", {}, session=s, redis=redis, registry=reg)
            narrowed = await proxy.call(
                ctx, "messages.send", {}, session=s, redis=redis, registry=reg)
            allowed = await proxy.call(
                ctx, "catalog.search", {}, session=s, redis=redis, registry=reg)
            await s.commit()
    finally:
        TOOL_PLAN_EXEMPT.pop("web_fetch", None)
    assert fetched.ok  # the fetch itself is allowed and flags the run untrusted
    assert narrowed.error is not None and narrowed.error.code == "permission_denied_manifest"
    assert allowed.ok  # catalog.search is on the narrowing allow-list


async def test_three_manifest_violations_abort_run(org: uuid.UUID) -> None:
    m, h = _manifest([{"name": "catalog.search", "read_only": True}])
    ctx = _ctx(org, m, h)
    redis = FakeRedis()
    async with org_scoped_session(org) as s:
        await proxy.call(ctx, "crm.write", {}, session=s, redis=redis, registry={})
        await proxy.call(ctx, "crm.write", {}, session=s, redis=redis, registry={})
        with pytest.raises(RunAborted) as exc:  # the 3rd violation aborts
            await proxy.call(ctx, "crm.write", {}, session=s, redis=redis, registry={})
        await s.commit()
    assert exc.value.violations == 3


async def test_manifest_integrity_failure_is_denied(org: uuid.UUID) -> None:
    m, _ = _manifest([{"name": "catalog.search"}])
    ctx = _ctx(org, m, "sha256:tampered")  # wrong hash
    redis = FakeRedis()
    async with org_scoped_session(org) as s:
        result = await proxy.call(ctx, "catalog.search", {}, session=s, redis=redis, registry={})
        await s.commit()
    assert result.error is not None and result.error.code == "permission_denied_manifest"


async def test_param_constraint_violation(org: uuid.UUID) -> None:
    m, h = _manifest([{"name": "pricing.compute",
                       "params_constraints": {"strategy": {"enum": ["formula_v1"]}}}])
    ctx = _ctx(org, m, h)
    redis = FakeRedis()
    async with org_scoped_session(org) as s:
        result = await proxy.call(
            ctx, "pricing.compute", {"strategy": "not_allowed"}, session=s, redis=redis,
            registry={},
        )
    assert result.error is not None and result.error.code == "config_schema_violation"


async def test_rate_limit_denies_second_call(org: uuid.UUID) -> None:
    m, h = _manifest([{"name": "catalog.search", "rate_limit": {"per_min": 1}}])
    ctx = _ctx(org, m, h)
    redis = FakeRedis()

    async def impl(c: RunContext, p: dict, s: Any, aid: uuid.UUID) -> Any:
        return {"hits": []}

    async with org_scoped_session(org) as s:
        first = await proxy.call(ctx, "catalog.search", {}, session=s, redis=redis,
                                 registry={"catalog.search": impl})
        second = await proxy.call(ctx, "catalog.search", {}, session=s, redis=redis,
                                  registry={"catalog.search": impl})
        await s.commit()
    assert first.ok
    assert second.error is not None and second.error.code == "rate_limited"


async def test_budget_checked_before_tier(org: uuid.UUID) -> None:
    m, h = _manifest(
        [{"name": "messages.send", "requires_tier_eval": True}], budgets={"sends_day": 5}
    )
    ctx = _ctx(org, m, h)
    redis = FakeRedis()
    from datetime import UTC, datetime
    day = datetime.now(UTC).strftime("%Y%m%d")
    redis.kv[f"gop:budget:{ctx.instance_id}:sends:{day}"] = 5  # exhausted
    async with org_scoped_session(org) as s:
        result = await proxy.call(ctx, "messages.send", {}, session=s, redis=redis, registry={})
    assert result.error is not None and result.error.code == "budget_exceeded"


async def test_tier_eval_returns_approval_pending(org: uuid.UUID) -> None:
    m, h = _manifest([{"name": "messages.send", "requires_tier_eval": True}])
    ctx = _ctx(org, m, h)
    redis = FakeRedis()
    async with org_scoped_session(org) as s:
        result = await proxy.call(ctx, "messages.send", {}, session=s, redis=redis, registry={})
    assert result.pending is not None and result.pending.tier >= 2
    assert result.ok is False


async def test_untrusted_narrowing_blocks_then_allows(org: uuid.UUID) -> None:
    m, h = _manifest([{"name": "catalog.search"}, {"name": "pricing.compute"}])
    redis = FakeRedis()

    async def impl(c: RunContext, p: dict, s: Any, aid: uuid.UUID) -> Any:
        return {"ok": True}

    async with org_scoped_session(org) as s:
        # under untrusted content, only narrowing-allowed tools pass
        blocked = await proxy.call(
            _ctx(org, m, h, untrusted=True), "pricing.compute", {"strategy": "x"},
            session=s, redis=redis, registry={},
        )
        allowed = await proxy.call(
            _ctx(org, m, h, untrusted=True), "catalog.search", {}, session=s, redis=redis,
            registry={"catalog.search": impl},
        )
        await s.commit()
    assert blocked.error is not None and blocked.error.code == "permission_denied_manifest"
    assert allowed.ok


async def test_successful_read_tool_executes_and_audits_intent(org: uuid.UUID) -> None:
    m, h = _manifest([{"name": "catalog.search", "read_only": True}])
    ctx = _ctx(org, m, h)
    redis = FakeRedis()
    seen = {}

    async def impl(c: RunContext, p: dict, s: Any, aid: uuid.UUID) -> Any:
        seen["audit_id"] = aid
        return {"results": [], "nearest": []}

    async with org_scoped_session(org) as s:
        result = await proxy.call(ctx, "catalog.search", {"query": "gold"}, session=s,
                                  redis=redis, registry={"catalog.search": impl})
        await s.commit()
    assert result.ok and result.output == {"results": [], "nearest": []}
    assert result.audit_id is not None and seen["audit_id"] == result.audit_id
    assert "tool.catalog.search:intent" in await _audit_actions(org)


async def test_landing_publish_parks_until_owner_approval(org: uuid.UUID) -> None:
    # LP-2d: an agent-initiated landing_page.publish is tier-gated. It PARKS for owner approval and
    # does NOT execute; only an approved (resumed) run skips the gate and runs the impl.
    m, h = _manifest([{"name": "landing_page.publish", "requires_tier_eval": True}])
    calls: list[dict] = []

    async def _publish(c: RunContext, p: dict, s: Any, aid: uuid.UUID) -> Any:
        calls.append(p)
        return {"status": "published"}

    reg = {"landing_page.publish": _publish}
    redis = FakeRedis()
    async with org_scoped_session(org) as s:
        parked = await proxy.call(
            _ctx(org, m, h), "landing_page.publish", {"page_id": "x"},
            session=s, redis=redis, registry=reg, tier_eval=lambda c, t, p: 2)
        approved_ctx = RunContext(
            org_id=org, run_id=uuid.uuid4(), instance_id=_INSTANCE[str(org)],
            manifest=m, manifest_hash=h, approved=frozenset({"landing_page.publish"}))
        ran = await proxy.call(
            approved_ctx, "landing_page.publish", {"page_id": "x"},
            session=s, redis=redis, registry=reg, tier_eval=lambda c, t, p: 2)
        await s.commit()
    # parked: pending approval at tier 2, no output
    assert parked.pending is not None and parked.pending.tier == 2 and parked.output is None
    # approved: the gate is skipped and the impl executes
    assert ran.ok and ran.output == {"status": "published"}
    # the impl ran EXACTLY once — only after approval; the parked call had no side effect
    assert len(calls) == 1
