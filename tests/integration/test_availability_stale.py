"""Availability transitions + price-input staleness (MVP-049) against real Postgres under app_rw.

Proves: an agent-actor transition is audited; a rule-referenced attribute edit (weight) flags the
open quote computed from that item while an unrelated edit (gender) does not; and only open
(draft, unexpired) quotes that actually reference the item are flagged. Skips when DB unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
import yaml

from core.catalog import availability, crud
from core.catalog.availability import AVAILABILITY_CHANGED_ACTION, InvalidTransition
from core.catalog.crud import ItemInput
from core.common import db as dbmod
from core.common.config import get_settings
from core.pricing import registry
from core.tenancy.middleware import org_scoped_session

VERTICALS = Path(__file__).resolve().parents[2] / "verticals"
SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "huid": {"type": "string"}, "purity": {"type": "string"},
        "net_weight_g": {"type": "string"}, "gender": {"type": "string"},
    },
})


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.quotes')"))
    finally:
        await conn.close()


class Scene:
    def __init__(self, org: uuid.UUID, pack_id: uuid.UUID, strategy_id: uuid.UUID) -> None:
        self.org = org
        self.pack_id = pack_id
        self.strategy_id = strategy_id

    async def item(self, huid: str, **attrs: str) -> uuid.UUID:
        async with org_scoped_session(self.org) as s:
            item_id, _ = await crud.create_item(
                s, self.org, ItemInput(title="Piece", price_mode="computed",
                                       attributes={"huid": huid, **attrs}),
                actor_id=uuid.uuid4(),
            )
            await s.commit()
        return item_id

    async def quote(
        self, *, item_id: uuid.UUID | None, status: str = "draft", expired: bool = False
    ) -> uuid.UUID:
        inputs = {"inputs": {"net_weight_g": "12.4"}, "params": {}}
        if item_id is not None:
            inputs["inputs"]["item_id"] = str(item_id)
        valid_until = datetime.now(UTC) - timedelta(hours=1) if expired else None
        conn = await asyncpg.connect(_dsn())
        try:
            return await conn.fetchval(
                "INSERT INTO quotes (org_id, strategy_id, rules_version, inputs, breakdown, "
                " total_minor, status, valid_until) "
                "VALUES ($1,$2,1,$3::jsonb,'[]'::jsonb,100,$4,$5) RETURNING id",
                self.org, self.strategy_id, json.dumps(inputs), status, valid_until,
            )
        finally:
            await conn.close()


async def _is_stale(quote_id: uuid.UUID) -> bool:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval("SELECT stale_inputs FROM quotes WHERE id=$1", quote_id)
    finally:
        await conn.close()


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/catalog+pricing not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    slug = f"jw{org.hex[:8]}"
    strategy = yaml.safe_load((VERTICALS / "jewelry" / "pricing" / "strategy.yaml").read_text())
    strategy["strategy_key"] = f"av_{org.hex[:8]}"
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'A')", org)
        pack_id = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature, "
            "status) VALUES ($1,'1','>=1','{}'::jsonb,'u','s','published') RETURNING id", slug,
        )
        await conn.execute(
            "INSERT INTO pack_installations (org_id, pack_id, status) VALUES ($1,$2,'active')",
            org, pack_id,
        )
        await conn.execute(
            "INSERT INTO catalog_schemas (pack_id, version, json_schema, identity_keys) "
            "VALUES ($1, 1, $2::jsonb, $3)", pack_id, SCHEMA, ["huid"],
        )
    finally:
        await conn.close()
    async with org_scoped_session(org) as s:
        strategy_id = await registry.load_strategy(s, pack_id, strategy)
        await s.commit()
    yield Scene(org, pack_id, strategy_id)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM quotes WHERE org_id=$1", org)
        await conn.execute("DELETE FROM pricing_strategies WHERE pack_id=$1", pack_id)
        await conn.execute("DELETE FROM catalog_items_history WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)  # cascades items
        await conn.execute("DELETE FROM catalog_schemas WHERE pack_id=$1", pack_id)
        await conn.execute("DELETE FROM packs WHERE id=$1", pack_id)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_agent_transition_updates_and_is_audited(scene: Scene) -> None:
    item_id = await scene.item("H-TRANS", net_weight_g="10.0")
    async with org_scoped_session(scene.org) as s:
        new_state = await availability.transition(
            s, scene.org, item_id, "out", actor_id=uuid.uuid4(), actor_type="agent",
            reason="sold",
        )
        await s.commit()
    assert new_state == "out"
    conn = await asyncpg.connect(_dsn())
    try:
        avail = await conn.fetchval("SELECT availability FROM catalog_items WHERE id=$1", item_id)
        audited = await conn.fetchval(
            "SELECT count(*) FROM audit_log WHERE org_id=$1 AND action=$2 AND actor_type='agent'",
            scene.org, AVAILABILITY_CHANGED_ACTION,
        )
    finally:
        await conn.close()
    assert avail == "out"
    assert audited == 1


async def test_invalid_transition_raises(scene: Scene) -> None:
    item_id = await scene.item("H-BAD", net_weight_g="10.0")
    async with org_scoped_session(scene.org) as s:
        with pytest.raises(InvalidTransition):
            await availability.transition(
                s, scene.org, item_id, "bookable_slot", actor_id=uuid.uuid4(),
            )


async def test_flag_only_open_referencing_quotes(scene: Scene) -> None:
    item_id = await scene.item("H-FLAG", net_weight_g="12.4")
    other = uuid.uuid4()
    linked = await scene.quote(item_id=item_id)
    other_item = await scene.quote(item_id=other)          # a different item
    sent = await scene.quote(item_id=item_id, status="sent")  # not open
    expired = await scene.quote(item_id=item_id, expired=True)  # past valid_until
    async with org_scoped_session(scene.org) as s:
        flagged = await availability.flag_stale_quotes_for_item(s, scene.org, item_id)
        await s.commit()
    assert flagged == 1
    assert await _is_stale(linked)
    assert not await _is_stale(other_item)
    assert not await _is_stale(sent)
    assert not await _is_stale(expired)


async def test_weight_edit_flags_dependent_quote_unrelated_edit_does_not(scene: Scene) -> None:
    item_id = await scene.item("H-EDIT", purity="22K", net_weight_g="12.4", gender="female")
    weight_quote = await scene.quote(item_id=item_id)

    # An unrelated attribute (gender is not read by any pricing rule) must NOT flag.
    async with org_scoped_session(scene.org) as s:
        await crud.update_item(
            s, scene.org, item_id, {"attributes": {"huid": "H-EDIT", "purity": "22K",
                                                    "net_weight_g": "12.4", "gender": "male"}},
            actor_id=uuid.uuid4(), reason="fix gender",
        )
        await s.commit()
    assert not await _is_stale(weight_quote)

    # Editing net_weight_g (a rule-referenced input) flags the dependent open quote.
    async with org_scoped_session(scene.org) as s:
        await crud.update_item(
            s, scene.org, item_id, {"attributes": {"huid": "H-EDIT", "purity": "22K",
                                                    "net_weight_g": "13.1", "gender": "male"}},
            actor_id=uuid.uuid4(), reason="reweigh",
        )
        await s.commit()
    assert await _is_stale(weight_quote)
