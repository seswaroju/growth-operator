"""Campaign SEND execute path (MVP-075 / C5) against real Postgres + the gated-simulated send().

Covers the two human moments and the machine discipline: the **typed-count gate** (409/mismatch),
the **tier-3 approval**, the **fan-out** that only messages consented + un-suppressed contacts via
the real ``send()`` (writing one ``campaign_sends`` row each and marking the campaign executed), a
**reject** that sends nothing, and the **quality-halt** on an opt-out spike. Skips if DB is down.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import asyncpg
import pytest

from core.campaigns import send as cs
from core.channels.whatsapp.credentials import store_credentials
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.middleware import org_scoped_session
from tests.conftest import entitle_org


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.campaign_sends')"))
    finally:
        await conn.close()


@dataclass
class Scene:
    org: uuid.UUID
    user: uuid.UUID
    campaign: uuid.UUID
    audience: list[uuid.UUID] = field(default_factory=list)   # the 2 sendable contacts
    excluded: list[uuid.UUID] = field(default_factory=list)   # suppressed + no-consent


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/campaign_sends not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org, user = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Campaigns')", org)
        # PLAN-5: paid execution follows the plan, so the fixture's store is subscribed.
        await entitle_org(conn, org)
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           user, f"{user}@example.test")
        pnid, waba = f"pn-{org.hex[:6]}", f"w-{org.hex[:6]}"
        channel_id = await conn.fetchval(
            "INSERT INTO channels (org_id, type, external_id, credentials_ref, waba_id) "
            "VALUES ($1,'whatsapp',$2,'channel_credentials',$3) RETURNING id", org, pnid, waba)
        await conn.execute(
            "INSERT INTO message_templates "
            "(org_id, channel_type, template_key, language, body, provider_status, category) "
            "VALUES ($1,'whatsapp','promo','en','Hello!','approved','marketing')", org)

        async def contact(consent: str, *, suppress: bool = False) -> uuid.UUID:
            cid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO contacts (id, org_id, phone, consent_status) VALUES ($1,$2,$3,$4)",
                cid, org, f"+91{cid.int % 10**10:010d}", consent)
            if suppress:
                await conn.execute(
                    "INSERT INTO suppressions (org_id, contact_id, scope, reason) "
                    "VALUES ($1,$2,'marketing','test')", org, cid)
            return cid

        a1 = await contact("opted_in")
        a2 = await contact("granted")
        x1 = await contact("opted_in", suppress=True)  # consented but suppressed → excluded
        x2 = await contact("unknown")                   # no positive consent → excluded
        camp = await conn.fetchval(
            "INSERT INTO campaigns (org_id, name, template_key, template_lang, created_by) "
            "VALUES ($1,'Promo','promo','en',$2) RETURNING id", org, user)
    finally:
        await conn.close()
    async with org_scoped_session(org) as s:  # encrypted channel creds → send()'s "connected" gate
        await store_credentials(
            s, org_id=org, channel_id=uuid.UUID(str(channel_id)),
            credentials={"waba_id": waba, "phone_number_id": pnid, "access_token": "tok"})
    yield Scene(org, user, uuid.UUID(str(camp)), audience=[a1, a2], excluded=[x1, x2])
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM campaign_sends WHERE org_id=$1", org)
        await conn.execute("DELETE FROM messages WHERE org_id=$1", org)
        await conn.execute("DELETE FROM conversations WHERE org_id=$1", org)
        # the fan-out minted append-only audit_log capabilities; drop them with the trigger off so
        # the org delete (which cascades) doesn't hit the immutability guard.
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER USER")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER USER")
        await conn.execute("DELETE FROM approvals WHERE org_id=$1", org)
        await conn.execute("DELETE FROM campaigns WHERE org_id=$1", org)
        await conn.execute("DELETE FROM suppressions WHERE org_id=$1", org)
        await conn.execute("DELETE FROM message_templates WHERE org_id=$1", org)
        await conn.execute("DELETE FROM contacts WHERE org_id=$1", org)
        await conn.execute("DELETE FROM channels WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM users WHERE id=$1", user)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _fetchval(org: uuid.UUID, sql: str, *args: object) -> object:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(sql, *args)
    finally:
        await conn.close()


async def test_typed_count_mismatch_blocks(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        with pytest.raises(cs.CountMismatch) as ei:
            await cs.request_campaign_send(
                s, scene.org, scene.campaign, recipient_count=99, requested_by=scene.user)
    assert ei.value.actual == 2  # the two consented, un-suppressed contacts


async def test_send_creates_tier3_approval(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        approval_id = await cs.request_campaign_send(
            s, scene.org, scene.campaign, recipient_count=2, requested_by=scene.user)
        await s.commit()
    tier = await _fetchval(scene.org, "SELECT tier FROM approvals WHERE id=$1", approval_id)
    status = await _fetchval(scene.org, "SELECT status FROM campaigns WHERE id=$1", scene.campaign)
    assert tier == 3 and status == "pending_approval"


async def test_approve_fans_out_and_marks_executed(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        await cs.request_campaign_send(
            s, scene.org, scene.campaign, recipient_count=2, requested_by=scene.user)
        await s.commit()
    # simulate the approval consumer: setup then fan out
    async with org_scoped_session(scene.org) as s:
        assert await cs.setup_campaign_execution(s, scene.org, scene.campaign) is True
        await s.commit()
    await cs.process_campaign_batch(scene.org, scene.campaign)

    sent = await _fetchval(
        scene.org, "SELECT count(*) FROM campaign_sends WHERE campaign_id=$1 AND status='sent'",
        scene.campaign)
    total = await _fetchval(
        scene.org, "SELECT count(*) FROM campaign_sends WHERE campaign_id=$1", scene.campaign)
    status = await _fetchval(scene.org, "SELECT status FROM campaigns WHERE id=$1", scene.campaign)
    sent_count = await _fetchval(
        scene.org, "SELECT sent_count FROM campaigns WHERE id=$1", scene.campaign)
    # only the 2 sendable contacts get a row (excluded ones are never queued), and both send
    assert total == 2 and sent == 2
    assert status == "executed" and sent_count == 2
    for x in scene.excluded:
        has = await _fetchval(
            scene.org, "SELECT count(*) FROM campaign_sends WHERE campaign_id=$1 AND contact_id=$2",
            scene.campaign, x)
        assert has == 0


async def test_reject_sends_nothing(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        await cs.request_campaign_send(
            s, scene.org, scene.campaign, recipient_count=2, requested_by=scene.user)
        await s.commit()
    async with org_scoped_session(scene.org) as s:
        await cs.mark_campaign_rejected(s, scene.org, scene.campaign)
        await s.commit()
    status = await _fetchval(scene.org, "SELECT status FROM campaigns WHERE id=$1", scene.campaign)
    rows = await _fetchval(
        scene.org, "SELECT count(*) FROM campaign_sends WHERE campaign_id=$1", scene.campaign)
    assert status == "rejected" and rows == 0


async def test_quality_halt_on_optout_spike(scene: Scene) -> None:
    # Seed 12 'sent' rows, then suppress > 10% of them → the halt reason fires; process halts.
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "UPDATE campaigns SET status='executing' WHERE id=$1", scene.campaign)
        contacts = []
        for _ in range(12):
            cid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO contacts (id, org_id, phone, consent_status) "
                "VALUES ($1,$2,$3,'granted')",
                cid, scene.org, f"+91{cid.int % 10**10:010d}")
            await conn.execute(
                "INSERT INTO campaign_sends (org_id, campaign_id, contact_id, status, sent_at) "
                "VALUES ($1,$2,$3,'sent', now())", scene.org, scene.campaign, cid)
            contacts.append(cid)
        # opt out 3 of the 12 (25% > 10%)
        for cid in contacts[:3]:
            await conn.execute(
                "INSERT INTO suppressions (org_id, contact_id, scope, reason) "
                "VALUES ($1,$2,'marketing','stop')", scene.org, cid)
    finally:
        await conn.close()
    await cs.process_campaign_batch(scene.org, scene.campaign)
    status = await _fetchval(scene.org, "SELECT status FROM campaigns WHERE id=$1", scene.campaign)
    halt = await _fetchval(
        scene.org, "SELECT halt_reason FROM campaigns WHERE id=$1", scene.campaign)
    assert status == "halted" and halt is not None and "opt-out" in halt
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "DELETE FROM contacts WHERE org_id=$1 AND id = ANY($2::uuid[])", scene.org, contacts)
    finally:
        await conn.close()
