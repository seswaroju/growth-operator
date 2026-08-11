"""End-to-end jewelry journey (≈MVP-097) — the §1 core loop over the REAL runtime + fake adapters.

Installs the jewelry pack for a fresh org, seeds a catalog piece + a fresh gold rate, and drives the
concierge run with a **scripted** `SimulatedModel` (no LLM, no network) through the real mediation
proxy + pricing engine: a price inquiry → `catalog.search` (grounds on the real item) →
`pricing.compute` (writes a quote **+ committed-figures ledger**) → a grounded reply. Asserts the
quote persists and every figure is matchable to the paise — the spine the send/approval legs extend.
Skips when the DB is unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import yaml

from core.catalog.crud import ItemInput, create_item
from core.channels.whatsapp.credentials import store_credentials
from core.common import db as dbmod
from core.common.config import get_settings
from core.mediation import manifest as manifest_mod
from core.packs.installer import install
from core.pricing import ledger, registry
from core.runtime.executor import resume_after_approval, start_run
from core.runtime.model import ModelResult, ToolCall
from core.tenancy.middleware import org_scoped_session

JEWELRY = Path(__file__).resolve().parents[2] / "verticals" / "jewelry"

# Deterministic pricing: 22K · 12.4g at ₹7,320/g, 8% making → total 10097032 minor (₹1,00,970.32).
STRATEGY = "formula_weight_rate_v1"
INPUTS = {"purity": "22K", "net_weight_g": "12.4", "stones": [], "requested_discount_minor": 0}
PARAMS = {"making_pct": 8, "making_min_minor": 50000, "wastage_pct": 0, "discount_ceiling_pct": 5}
RATE_22K = 732000
EXPECTED_TOTAL = 10097032


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.agent_runs')")) and bool(
            await conn.fetchval("SELECT to_regclass('public.quotes')"))
    finally:
        await conn.close()


async def _no_kill(org_id: uuid.UUID) -> bool:
    return False


@dataclass
class Journey:
    org: uuid.UUID
    instance: uuid.UUID
    conversation: uuid.UUID


@pytest.fixture()
async def journey() -> AsyncIterator[Journey]:
    if not await _db_ready():
        pytest.skip("Postgres/runtime not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    actor = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Ratna')", org)
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           actor, f"owner+{actor.hex[:8]}@example.test")
    finally:
        await conn.close()

    # Install the pack (registers the strategy, catalog schema, bindings, approval policies).
    result = await install(org, JEWELRY, {})
    conn = await asyncpg.connect(_dsn())
    try:
        pack_id = await conn.fetchval(
            "SELECT pack_id FROM pack_installations WHERE id=$1", result.installation_id)
        # NOTE: install() does NOT register the pack's pricing strategy or rate source (a go-live
        # gap — flagged). Both are GLOBAL (pack-keyed), so register idempotently; leave them at
        # teardown. The rate source may already exist from a prior run.
        source_id = await conn.fetchval(
            "INSERT INTO rate_sources (pack_id, source_key, fetch_spec, staleness_max) "
            "VALUES ($1,'ibja_gold','{}'::jsonb, interval '24 hours') "
            "ON CONFLICT (pack_id, source_key) DO UPDATE SET source_key = EXCLUDED.source_key "
            "RETURNING id", pack_id)
        snapshot_id = await conn.fetchval(
            "INSERT INTO rate_snapshots (source_id, value, captured_at) "
            "VALUES ($1,$2::jsonb, now()) RETURNING id",
            source_id, json.dumps({"22K": RATE_22K}))
        # Activate the concierge instance (install leaves instances paused until go-live).
        instance = await conn.fetchval(
            "UPDATE agent_instances ai SET status='active' "
            "FROM agent_bindings ab JOIN agent_archetypes ar ON ar.id = ab.archetype_id "
            "WHERE ai.binding_id = ab.id AND ai.org_id = $1 AND ar.slug = 'concierge' "
            "RETURNING ai.id", org)
        # A contact + conversation for the inbound thread.
        channel = await conn.fetchval(
            "INSERT INTO channels (org_id, type, external_id, credentials_ref, status) "
            "VALUES ($1,'whatsapp',$2,'vault://x','active') RETURNING id", org, f"pn-{org.hex[:8]}")
        contact = await conn.fetchval(
            "INSERT INTO contacts (org_id, phone, consent_status) VALUES ($1,$2,'explicit') "
            "RETURNING id", org, "+919000000000")
        conversation = await conn.fetchval(
            "INSERT INTO conversations (org_id, contact_id, channel_id, status) "
            "VALUES ($1,$2,$3,'open') RETURNING id", org, contact, channel)
    finally:
        await conn.close()
    assert instance is not None, "concierge instance 'priya' not created by install"

    # Register the jewelry pricing strategy (install doesn't) so pricing.compute resolves it, and
    # connect the WhatsApp channel (store creds) so the gated send sees it as connected.
    strategy_def = yaml.safe_load((JEWELRY / "pricing" / "strategy.yaml").read_text())
    async with org_scoped_session(org) as s:
        await registry.load_strategy(s, pack_id, strategy_def)
        await store_credentials(
            s, org_id=org, channel_id=channel,
            credentials={"phone_number_id": "PN1", "access_token": "tok", "waba_id": "WABA1"})
        await s.commit()

    # Compile + sign the instance's permission manifest (install leaves it a raw placeholder — the
    # auto-recompile was deferred in MVP-061), else the proxy denies every tool ("integrity fail").
    async with org_scoped_session(org) as s:
        await manifest_mod.recompile_instance(s, org, instance)
        await s.commit()

    # A real catalog piece so catalog.search grounds on something.
    async with org_scoped_session(org) as s:
        await create_item(
            s, org,
            ItemInput(title="Classic 22K gold chain", price_mode="computed",
                      attributes={"category": "chain", "metal": "gold", "purity": "22K",
                                  "gross_weight_g": 13.0, "net_weight_g": 12.4}),
            actor_id=actor)

    yield Journey(org, instance, conversation)

    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        # Clean ONLY what this test owns: our rate snapshot + the org (its delete cascades the
        # org-scoped rows). The pack, rate sources and bindings are global/shared — leave them.
        # BUT the pack's approval_policies (scope='pack') MUST go: the tier engine reads policies by
        # action_type without a pack filter (correct for the single-pack MVP — one shared pack), so
        # leaving them behind leaks the jewelry tier-2 send policy into every other test's tier
        # evaluation. install() re-seeds them next run (idempotent NOT EXISTS guard).
        await conn.execute("DELETE FROM approval_policies WHERE pack_id=$1", pack_id)
        await conn.execute("DELETE FROM rate_snapshots WHERE id=$1", snapshot_id)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM users WHERE id=$1", actor)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


# The grounded reply — carries the EXACT ledgered total (₹1,00,970.32), so it passes the send-path
# figure gate and, being a ≥₹1L quote, parks for the owner's approval.
GROUNDED_REPLY = "Your quote for the 22K chain: total ₹1,00,970.32, valid for 24 hours."


class ConciergeModel:
    """Deterministic (no LLM), stateless over `tool_calls_made` so it drives both the start run and
    the resume after approval: search catalog → compute quote → reply with the exact figure."""

    async def turn(self, *, node_key: str, prompt: str, context: dict[str, Any]) -> ModelResult:
        made = int(context.get("tool_calls_made", 0))
        if made == 0:
            return ModelResult(
                tool_call=ToolCall("catalog.search", {"query": "22K gold chain"}), text=None)
        if made == 1:
            return ModelResult(
                tool_call=ToolCall("pricing.compute",
                                   {"strategy": STRATEGY, "inputs": INPUTS, "params": PARAMS}),
                text=None)
        return ModelResult(tool_call=None, text=GROUNDED_REPLY)


async def _one_pending_approval(org: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID] | None:
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT id, run_id FROM approvals WHERE org_id=$1 AND status='pending'", org)
        return (row["id"], row["run_id"]) if row else None
    finally:
        await conn.close()


async def test_full_journey_quote_park_approve_send(journey: Journey) -> None:
    # 1–4. inquiry → catalog.search (grounds) → pricing.compute (quote + ledger) → priced reply.
    outcome = await start_run(
        journey.org, journey.instance, trigger="msg.received",
        input={"text": "price for a 22K gold chain, 12.4g?"},
        conversation_id=journey.conversation, model=ConciergeModel(), kill_switch=_no_kill)

    # A ₹1L+ quote reply is high-value → the run PARKS for the owner's approval (HITL, §19).
    assert outcome.status == "interrupted"

    conn = await asyncpg.connect(_dsn())
    try:
        total = await conn.fetchval(
            "SELECT total_minor FROM quotes WHERE org_id=$1 ORDER BY created_at DESC LIMIT 1",
            journey.org)
    finally:
        await conn.close()
    assert total == EXPECTED_TOTAL  # a real quote from the real catalog + rate

    # The total is matchable to the paise (committed-figures ledger) — grounding is real.
    async with org_scoped_session(journey.org) as s:
        assert await ledger.match(s, journey.org, EXPECTED_TOTAL)
        assert not await ledger.match(s, journey.org, EXPECTED_TOTAL + 1)  # off-by-one fails closed

    pending = await _one_pending_approval(journey.org)
    assert pending is not None, "the priced reply should have parked an approval"
    _, run_id = pending

    # 5. Owner approves → the run resumes and the (grounded) reply is sent through the gated path.
    resumed = await resume_after_approval(
        run_id, journey.org, decision="approve", model=ConciergeModel())
    assert resumed.status == "succeeded"

    # The reply was recorded as an outbound message with its provider id (simulated send).
    conn = await asyncpg.connect(_dsn())
    try:
        sent = await conn.fetchrow(
            "SELECT status, body, provider_message_id FROM messages "
            "WHERE org_id=$1 AND direction='outbound' ORDER BY created_at DESC LIMIT 1",
            journey.org)
    finally:
        await conn.close()
    assert sent is not None and "1,00,970.32" in sent["body"]  # the grounded figure went out
    assert sent["status"] == "sent" and sent["provider_message_id"]  # (simulated) wamid, sent once
