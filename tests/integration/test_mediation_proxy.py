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

    async def incr(self, key: str) -> int:
        self.kv[key] = int(self.kv.get(key, 0)) + 1
        return self.kv[key]

    async def expire(self, key: str, secs: int) -> bool:
        return True

    async def get(self, key: str) -> Any:
        return self.kv.get(key)

    async def set(self, key: str, value: Any, **kw: Any) -> bool:
        self.kv[key] = value
        return True

    async def xadd(self, stream: str, fields: dict[str, Any]) -> str:
        self.streams.append((stream, fields))
        return "1-1"


def _manifest(tools: list[dict], *, budgets: dict | None = None) -> tuple[dict, str]:
    m = {
        "manifest_version": 3, "tools": tools,
        "budgets": budgets or {"sends_day": 300},
        "untrusted_narrowing": {"allow": ["catalog.search"]},
    }
    return m, proxy._manifest_hash(m)


@pytest.fixture()
async def org() -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres/audit not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org_id = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'M')", org_id)
    finally:
        await conn.close()
    yield org_id
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", org_id)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM organizations WHERE id=$1", org_id)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _ctx(org_id: uuid.UUID, manifest: dict, digest: str, *, untrusted: bool = False) -> RunContext:
    return RunContext(
        org_id=org_id, run_id=uuid.uuid4(), instance_id=uuid.uuid4(),
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
