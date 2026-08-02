"""Send-path figure check — Gate 5 (MVP-054) against real Postgres under app_rw.

The last line of defence: a rupee amount in the outbound text must match an unexpired ledger
row (MVP-053) or the send is refused with ``unledgered_figure`` and **no** Meta call happens.
Also covers warn mode (allowed, no raise) and the audited tier-3 owner override. Skips when the
DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from core.audit.writer import AuditEntry, write
from core.channels.whatsapp.credentials import store_credentials
from core.channels.whatsapp.meta_client import SendResult
from core.channels.whatsapp.send import FIGURE_OVERRIDE_ACTION, SendRefused, send
from core.common import db as dbmod
from core.common.config import get_settings
from core.pricing import ledger
from core.tenancy.middleware import org_scoped_session

LEDGERED_MINOR = 10_097_032  # ₹1,00,970.32
LEDGERED_BODY = "Your total is ₹1,00,970.32 — shall I reserve it?"
UNLEDGERED_BODY = "Special festival price just ₹99,999 today!"


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        ok = await conn.fetchval("SELECT to_regclass('public.committed_figures_ledger')")
        return bool(ok)
    finally:
        await conn.close()


class FakeMeta:
    """Records Meta calls so a refused send can assert the wire was never touched."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    @property
    def simulated(self) -> bool:
        return True

    async def send_text(
        self, phone_number_id: str, access_token: str, to: str, body: str
    ) -> SendResult:
        self.calls.append((to, body))
        return SendResult(ok=True, provider_message_id="wamid.OK")


class Scene:
    def __init__(self, org: uuid.UUID, channel_id: uuid.UUID) -> None:
        self.org = org
        self.channel_id = channel_id

    async def conversation(self) -> uuid.UUID:
        conn = await asyncpg.connect(_dsn())
        try:
            contact = await conn.fetchval(
                "INSERT INTO contacts (org_id, phone, consent_status) VALUES ($1,$2,'opted_in') "
                "RETURNING id",
                self.org, f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            )
            return await conn.fetchval(
                "INSERT INTO conversations (org_id, contact_id, channel_id) VALUES ($1,$2,$3) "
                "RETURNING id",
                self.org, contact, self.channel_id,
            )
        finally:
            await conn.close()

    async def mint_audit(self, conversation_id: uuid.UUID) -> uuid.UUID:
        async with org_scoped_session(self.org) as s:
            aid = await write(
                s,
                AuditEntry(
                    org_id=self.org, actor_type="user", actor_id=str(uuid.uuid4()),
                    action="msg.send", resource=str(conversation_id), payload={},
                ),
            )
        return aid.id

    async def ledger(self, amount_minor: int) -> None:
        async with org_scoped_session(self.org) as s:
            await ledger.write(
                s, self.org, [ledger.Figure(figure_type="total", amount_minor=amount_minor)],
                source_ref=uuid.uuid4(), expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
            await s.commit()


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/pricing (013) + messaging not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    pnid = f"pn-{org.hex[:8]}"
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'F')", org)
        channel_id = await conn.fetchval(
            "INSERT INTO channels (org_id, type, external_id, credentials_ref) "
            "VALUES ($1,'whatsapp',$2,'channel_credentials') RETURNING id",
            org, pnid,
        )
    finally:
        await conn.close()
    async with org_scoped_session(org) as s:
        await store_credentials(
            s, org_id=org, channel_id=channel_id,
            credentials={"waba_id": "w1", "phone_number_id": pnid, "access_token": "tok"},
        )
    yield Scene(org, channel_id)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM committed_figures_ledger WHERE org_id=$1", org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)  # cascades the rest
        await conn.execute("DELETE FROM event_outbox WHERE org_id=$1", org)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _override_audits(org: uuid.UUID) -> int:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM audit_log WHERE org_id=$1 AND action=$2",
            org, FIGURE_OVERRIDE_ACTION,
        )
    finally:
        await conn.close()


async def test_ledgered_figure_is_allowed(scene: Scene) -> None:
    conv = await scene.conversation()
    await scene.ledger(LEDGERED_MINOR)
    audit = await scene.mint_audit(conv)
    meta = FakeMeta()
    outcome = await send(
        org_id=scene.org, conversation_id=conv, body=LEDGERED_BODY,
        audit_id=audit, execution_token="t", meta_client=meta, message_class="transactional",
    )
    assert outcome.sent is True
    assert len(meta.calls) == 1  # matched the ledger -> went out


async def test_unledgered_figure_blocks_and_never_hits_the_wire(scene: Scene) -> None:
    conv = await scene.conversation()
    await scene.ledger(LEDGERED_MINOR)  # a different amount is ledgered
    audit = await scene.mint_audit(conv)
    meta = FakeMeta()
    with pytest.raises(SendRefused) as ei:
        await send(
            org_id=scene.org, conversation_id=conv, body=UNLEDGERED_BODY,
            audit_id=audit, execution_token="t", meta_client=meta, message_class="transactional",
        )
    assert ei.value.code == "unledgered_figure"
    assert meta.calls == []  # blocked before any Meta call


async def test_partial_match_still_blocks(scene: Scene) -> None:
    conv = await scene.conversation()
    await scene.ledger(LEDGERED_MINOR)
    audit = await scene.mint_audit(conv)
    meta = FakeMeta()
    body = "Total ₹1,00,970.32, and a bonus ₹5,000 gift card."  # 2nd figure unledgered
    with pytest.raises(SendRefused) as ei:
        await send(
            org_id=scene.org, conversation_id=conv, body=body,
            audit_id=audit, execution_token="t", meta_client=meta, message_class="transactional",
        )
    assert ei.value.code == "unledgered_figure"
    assert meta.calls == []


async def test_warn_mode_allows_unledgered_figure(scene: Scene) -> None:
    conv = await scene.conversation()
    audit = await scene.mint_audit(conv)
    meta = FakeMeta()
    outcome = await send(
        org_id=scene.org, conversation_id=conv, body=UNLEDGERED_BODY,
        audit_id=audit, execution_token="t", meta_client=meta, message_class="transactional",
        figure_check="warn",
    )
    assert outcome.sent is True  # warn does not block
    assert len(meta.calls) == 1


async def test_tier3_override_proceeds_and_is_audited(scene: Scene) -> None:
    conv = await scene.conversation()
    audit = await scene.mint_audit(conv)
    owner = uuid.uuid4()
    meta = FakeMeta()
    assert await _override_audits(scene.org) == 0
    outcome = await send(
        org_id=scene.org, conversation_id=conv, body=UNLEDGERED_BODY,
        audit_id=audit, execution_token="t", meta_client=meta, message_class="transactional",
        figure_override_by=owner,
    )
    assert outcome.sent is True
    assert len(meta.calls) == 1
    assert await _override_audits(scene.org) == 1  # override recorded on the audit chain
