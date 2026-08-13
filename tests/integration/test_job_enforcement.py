"""Background-job entitlement enforcement (PLAN-5).

A job is gated on what it *does*, not on what data it touches. Business processing on a tenant's
behalf follows the plan; cleanup, integrity and platform infrastructure must keep running even after
cancellation, or storage hygiene and correctness would decay with billing state.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.campaigns')"))
    finally:
        await conn.close()


class Jobs:
    def __init__(self, conn: asyncpg.Connection, tag: str) -> None:
        self.conn, self.tag = conn, tag
        self.org = uuid.uuid4()
        self.plan = uuid.uuid4()

    async def setup(self, capabilities: list[str]) -> None:
        await self.conn.execute(
            "INSERT INTO organizations (id, name, vertical) VALUES ($1,$2,'jewelry')",
            self.org, self.tag)
        await self.conn.execute(
            "INSERT INTO billing_plans (id, name, price_minor, features, config) "
            "VALUES ($1,$2,1,'[]'::jsonb,$3::jsonb)",
            self.plan, f"{self.tag}-plan",
            json.dumps({"entitlement_schema_version": 1, "entitlements": capabilities,
                        "agents": [], "channels": ["whatsapp"], "addons": [],
                        "promotions": [], "vertical": None}))
        await self.conn.execute(
            "INSERT INTO billing_subscriptions (org_id, plan_id, status) VALUES ($1,$2,'active')",
            self.org, self.plan)

    async def set_capabilities(self, capabilities: list[str]) -> None:
        await self.conn.execute(
            "UPDATE billing_plans SET config = jsonb_set(config, '{entitlements}', $2::jsonb) "
            "WHERE id = $1", self.plan, json.dumps(capabilities))

    async def campaign(self, status: str = "executing") -> uuid.UUID:
        return await self.conn.fetchval(
            "INSERT INTO campaigns (org_id, name, status, template_key) "
            "VALUES ($1,$2,$3,'t') RETURNING id", self.org, f"{self.tag}-c", status)

    async def campaign_state(self, cid: uuid.UUID) -> tuple[str, str | None]:
        row = await self.conn.fetchrow(
            "SELECT status, halt_reason FROM campaigns WHERE id = $1", cid)
        return row["status"], row["halt_reason"]


@pytest.fixture()
async def jobs() -> AsyncIterator[Jobs]:
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    conn = await asyncpg.connect(_dsn())
    j = Jobs(conn, f"job-{uuid.uuid4().hex[:8]}")
    try:
        yield j
    finally:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", j.org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM campaigns WHERE org_id=$1", j.org)
        await conn.execute("DELETE FROM billing_subscriptions WHERE org_id=$1", j.org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", j.org)
        await conn.execute("DELETE FROM billing_plans WHERE id=$1", j.plan)
        await conn.close()
        await dbmod.get_engine().dispose()
        dbmod.get_engine.cache_clear()
        dbmod.get_sessionmaker.cache_clear()


# ---- Business execution follows the plan -------------------------------------------------------


async def test_recovery_sweep_skips_unentitled_org(jobs: Jobs) -> None:
    """Ghost recovery is business processing: an unentitled store stops being swept, while its
    existing lead history stays readable."""
    from core.customers.recovery import run_recovery_sweep

    await jobs.setup(["customers"])                      # no ghost_recovery
    swept: list[uuid.UUID] = []

    import core.customers.recovery as mod

    original = mod.sweep_org

    async def spy(session, org_id):                      # type: ignore[no-untyped-def]
        swept.append(org_id)
        return await original(session, org_id)

    mod.sweep_org = spy                                  # type: ignore[assignment]
    try:
        await run_recovery_sweep()
    finally:
        mod.sweep_org = original                         # type: ignore[assignment]
    assert jobs.org not in swept


async def test_recovery_sweep_processes_an_entitled_org(jobs: Jobs) -> None:
    from core.customers.recovery import run_recovery_sweep

    await jobs.setup(["customers", "ghost_recovery"])
    swept: list[uuid.UUID] = []

    import core.customers.recovery as mod

    original = mod.sweep_org

    async def spy(session, org_id):                      # type: ignore[no-untyped-def]
        swept.append(org_id)
        return await original(session, org_id)

    mod.sweep_org = spy                                  # type: ignore[assignment]
    try:
        await run_recovery_sweep()
    finally:
        mod.sweep_org = original                         # type: ignore[assignment]
    assert jobs.org in swept


async def test_fanout_halts_revoked_campaign(jobs: Jobs) -> None:
    """Halting once through the existing mechanism is what stops the hourly job hot-looping."""
    from core.campaigns import send

    await jobs.setup(["campaigns.whatsapp", "customers"])
    cid = await jobs.campaign()
    await jobs.set_capabilities(["customers"])           # revoked mid-flight

    await send.process_campaign_batch(jobs.org, cid)
    status, reason = await jobs.campaign_state(cid)
    assert status == "halted"
    assert reason == "entitlement_revoked"


async def test_a_halted_campaign_is_not_retried(jobs: Jobs) -> None:
    from core.campaigns import send

    await jobs.setup(["campaigns.whatsapp", "customers"])
    cid = await jobs.campaign()
    await jobs.set_capabilities(["customers"])
    await send.process_campaign_batch(jobs.org, cid)
    first = await jobs.campaign_state(cid)
    await send.process_campaign_batch(jobs.org, cid)            # the next hourly tick
    assert await jobs.campaign_state(cid) == first       # idempotent, no churn


async def test_an_entitled_campaign_is_not_halted_by_entitlement(jobs: Jobs) -> None:
    from core.campaigns import send

    await jobs.setup(["campaigns.whatsapp", "customers"])
    cid = await jobs.campaign()
    await send.process_campaign_batch(jobs.org, cid)
    _status, reason = await jobs.campaign_state(cid)
    assert reason != "entitlement_revoked"


# ---- Maintenance and platform jobs keep running ------------------------------------------------


async def test_the_import_reaper_is_not_entitlement_gated(jobs: Jobs) -> None:
    """Storage hygiene must survive cancellation — otherwise cancelled tenants accumulate blobs."""
    from core.tenancy.enforcement import capability_surfaces

    reaper = next(
        s for s in capability_surfaces("catalog.ingestion") if s.id == "job.import_batch_reaper")
    assert reaper.exemption_reason and not reaper.enforcement
    assert reaper.action == "maintenance"


async def test_rate_provider_ingestion_is_platform_infrastructure(jobs: Jobs) -> None:
    """`rate_snapshots` is global (no org_id, no RLS), so ingestion is not a tenant operation."""
    from core.tenancy.enforcement import capability_surfaces

    ingest = next(
        s for s in capability_surfaces("jewelry.rate_operations")
        if s.id == "job.rate_provider_ingest")
    assert ingest.exemption_reason and not ingest.enforcement
