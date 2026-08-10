"""Owner-built activation + trust ledger (MVP-073g) against real Postgres.

An owner-built draft cannot self-activate: `request_activation` runs a simulation and raises a
tier-2 `workflow.activate` approval (report attached) while the definition stays a draft; the
resolved approval activates it (approve) or leaves it a draft (reject). Trust is the count of clean
(completed) runs, earning autonomy at the threshold. Pure-logic tests need no DB; persistence skips.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.middleware import org_scoped_session
from core.workflows import activation, authoring

# ---- pure trust logic (no DB) ---------------------------------------------------------


def test_trust_earns_at_threshold() -> None:
    below = activation._trust(activation.TRUST_THRESHOLD - 1)
    assert below["earned"] is False and below["tier_floor"] == activation.ACTIVATE_TIER
    at = activation._trust(activation.TRUST_THRESHOLD)
    assert at["earned"] is True and at["tier_floor"] is None


# ---- persistence (DB) -----------------------------------------------------------------


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.workflow_definitions')"))
    finally:
        await conn.close()


_DSL = {
    "workflow": "owner_activate", "version": 1,
    "trigger": {"event": {"type": "lead.stage.changed"}},
    "steps": [{"agent_task": {"archetype": "nurture", "task": "nudge"}}],
}


@pytest.fixture()
async def org() -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    oid = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Activate')", oid)
    finally:
        await conn.close()
    yield oid
    conn = await asyncpg.connect(_dsn())
    try:
        for t in ("workflow_runs", "workflow_definitions", "approvals", "event_outbox"):
            await conn.execute(f"DELETE FROM {t} WHERE org_id=$1", oid)
        await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _create_draft(org: uuid.UUID) -> uuid.UUID:
    async with org_scoped_session(org) as s:
        def_id = await authoring.create_owner_definition(s, org, _DSL)
        await s.commit()
    return def_id


async def _status(org: uuid.UUID, def_id: uuid.UUID) -> str:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval("SELECT status FROM workflow_definitions WHERE id=$1", def_id)
    finally:
        await conn.close()


async def test_request_activation_raises_tier2_approval_and_stays_draft(org: uuid.UUID) -> None:
    def_id = await _create_draft(org)
    async with org_scoped_session(org) as s:
        result = await activation.request_activation(s, org, def_id)
        await s.commit()
    assert "approval_id" in result and "simulation" in result and "trust" in result
    assert result["trust"]["earned"] is False  # brand-new draft, no clean runs
    assert await _status(org, def_id) == "draft"  # NOT activated by the request alone
    conn = await asyncpg.connect(_dsn())
    try:
        appr = await conn.fetchrow(
            "SELECT action_type, tier, payload FROM approvals WHERE org_id=$1", org)
    finally:
        await conn.close()
    assert appr["action_type"] == activation.ACTIVATE_ACTION
    assert appr["tier"] == activation.ACTIVATE_TIER
    payload = appr["payload"] if isinstance(appr["payload"], dict) else json.loads(appr["payload"])
    assert payload["definition_id"] == str(def_id)
    assert "simulation" in payload  # the report is attached to the approval


async def test_apply_decision_activates_on_approve(org: uuid.UUID) -> None:
    def_id = await _create_draft(org)
    async with org_scoped_session(org) as s:
        await activation.apply_activation_decision(s, org, def_id, approved=True)
        await s.commit()
    assert await _status(org, def_id) == "active"


async def test_apply_decision_reject_leaves_draft(org: uuid.UUID) -> None:
    def_id = await _create_draft(org)
    async with org_scoped_session(org) as s:
        await activation.apply_activation_decision(s, org, def_id, approved=False)
        await s.commit()
    assert await _status(org, def_id) == "draft"


async def test_activation_rejected_for_already_active(org: uuid.UUID) -> None:
    def_id = await _create_draft(org)
    async with org_scoped_session(org) as s:
        await activation.apply_activation_decision(s, org, def_id, approved=True)
        await s.commit()
    async with org_scoped_session(org) as s:
        with pytest.raises(activation.ActivationError):
            await activation.request_activation(s, org, def_id)


async def test_trust_status_counts_completed_runs(org: uuid.UUID) -> None:
    def_id = await _create_draft(org)
    conn = await asyncpg.connect(_dsn())
    try:
        for _ in range(2):
            await conn.execute(
                "INSERT INTO workflow_runs (org_id, definition_id, definition_version, status) "
                "VALUES ($1,$2,1,'completed')", org, def_id)
        await conn.execute(  # a non-completed run must not count as clean
            "INSERT INTO workflow_runs (org_id, definition_id, definition_version, status) "
            "VALUES ($1,$2,1,'waiting')", org, def_id)
    finally:
        await conn.close()
    async with org_scoped_session(org) as s:
        trust = await activation.owner_trust_status(s, org, def_id)
    assert trust["clean_runs"] == 2 and trust["earned"] is False
