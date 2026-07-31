"""Gated outbound send adapter (MVP-034 + MVP-036) against real Postgres under app_rw.

Covers the four gates (audit capability / execution token / suppression / consent) each
refusing with the right canonical code and making **no** Meta call, plus the happy path
(msg.sent.v1 + audit outcome), 429 Retry-After honouring, 5xx bounded retries → msg.failed.v1,
and transactional exemption from marketing consent/suppression. Meta is faked so failure modes
are deterministic and no network I/O happens. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from core.audit.writer import AuditEntry, write
from core.channels.whatsapp import send as send_mod
from core.channels.whatsapp.credentials import store_credentials
from core.channels.whatsapp.meta_client import SendResult
from core.channels.whatsapp.send import SendRefused, send
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.middleware import org_scoped_session


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        creds = await conn.fetchval("SELECT to_regclass('public.channel_credentials')")
        audit = await conn.fetchval("SELECT to_regclass('public.audit_log')")
        return bool(creds) and bool(audit)
    finally:
        await conn.close()


class FakeMeta:
    """Deterministic stand-in for MetaClient: returns queued results, repeating the last."""

    def __init__(self, results: list[SendResult]) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, str]] = []

    @property
    def simulated(self) -> bool:
        return True

    async def send_text(
        self, phone_number_id: str, access_token: str, to: str, body: str
    ) -> SendResult:
        self.calls.append((to, body))
        return self._results.pop(0) if len(self._results) > 1 else self._results[0]


class RecordingSleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


class Scene:
    def __init__(self, org: uuid.UUID, channel_id: uuid.UUID) -> None:
        self.org = org
        self.channel_id = channel_id

    async def conversation(self, *, consent: str = "opted_in") -> uuid.UUID:
        conn = await asyncpg.connect(_dsn())
        try:
            contact = await conn.fetchval(
                "INSERT INTO contacts (org_id, phone, consent_status) "
                "VALUES ($1,$2,$3) RETURNING id",
                self.org, f"+1555{uuid.uuid4().int % 10_000_000:07d}", consent,
            )
            return await conn.fetchval(
                "INSERT INTO conversations (org_id, contact_id, channel_id) "
                "VALUES ($1,$2,$3) RETURNING id",
                self.org, contact, self.channel_id,
            )
        finally:
            await conn.close()

    async def suppress(self, conversation_id: uuid.UUID, scope: str = "marketing") -> None:
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute(
                "INSERT INTO suppressions (org_id, contact_id, scope) "
                "SELECT org_id, contact_id, $2 FROM conversations WHERE id=$1",
                conversation_id, scope,
            )
        finally:
            await conn.close()

    async def mint_audit(
        self, conversation_id: uuid.UUID, *, action: str = "msg.send"
    ) -> uuid.UUID:
        async with org_scoped_session(self.org) as s:
            aid = await write(
                s,
                AuditEntry(
                    org_id=self.org, actor_type="user", actor_id=str(uuid.uuid4()),
                    action=action, resource=str(conversation_id), payload={},
                ),
            )
        return aid.id


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/messaging+audit+channel_credentials not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    pnid = f"pn-{org.hex[:8]}"
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'S')", org)
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
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)  # cascades the rest
        await conn.execute("DELETE FROM event_outbox WHERE org_id=$1", org)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _outbox_types(org: uuid.UUID) -> list[str]:
    conn = await asyncpg.connect(_dsn())
    try:
        return [r["type"] for r in await conn.fetch(
            "SELECT type FROM event_outbox WHERE org_id=$1 ORDER BY created_at", org
        )]
    finally:
        await conn.close()


async def _message_status(message_id: uuid.UUID) -> tuple[str, str | None]:
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT status, provider_message_id FROM messages WHERE id=$1", message_id
        )
        return row["status"], row["provider_message_id"]
    finally:
        await conn.close()


async def test_success_emits_sent_and_records_outcome(scene: Scene) -> None:
    conv = await scene.conversation(consent="opted_in")
    audit = await scene.mint_audit(conv)
    meta = FakeMeta([SendResult(ok=True, provider_message_id="wamid.TEST")])

    outcome = await send(
        org_id=scene.org, conversation_id=conv, body="hello",
        audit_id=audit, execution_token="exec-tok", meta_client=meta,
    )

    assert outcome.sent is True and outcome.provider_message_id == "wamid.TEST"
    assert len(meta.calls) == 1  # one Meta call
    assert await _message_status(outcome.message_id) == ("sent", "wamid.TEST")  # type: ignore[arg-type]
    assert "msg.sent.v1" in await _outbox_types(scene.org)

    conn = await asyncpg.connect(_dsn())
    try:
        actions = [r["action"] for r in await conn.fetch(
            "SELECT action FROM audit_log WHERE org_id=$1 ORDER BY seq", scene.org
        )]
    finally:
        await conn.close()
    assert actions == ["msg.send", "msg.send:succeeded"]  # intent then outcome


async def test_missing_audit_id_refused_no_http(scene: Scene) -> None:
    conv = await scene.conversation()
    meta = FakeMeta([SendResult(ok=True)])
    with pytest.raises(SendRefused) as ei:
        await send(
            org_id=scene.org, conversation_id=conv, body="x",
            audit_id=None, execution_token="exec-tok", meta_client=meta,
        )
    assert ei.value.code == "approval_required"
    assert meta.calls == []  # never touched the wire


async def test_bad_token_refused_no_http(scene: Scene) -> None:
    conv = await scene.conversation()
    audit = await scene.mint_audit(conv)
    meta = FakeMeta([SendResult(ok=True)])
    with pytest.raises(SendRefused) as ei:
        await send(
            org_id=scene.org, conversation_id=conv, body="x",
            audit_id=audit, execution_token="", meta_client=meta,
        )
    assert ei.value.code == "approval_required"
    assert meta.calls == []


async def test_suppressed_marketing_blocked_transactional_allowed(scene: Scene) -> None:
    conv = await scene.conversation(consent="opted_in")
    await scene.suppress(conv, scope="marketing")
    audit = await scene.mint_audit(conv)
    meta = FakeMeta([SendResult(ok=True, provider_message_id="wamid.T")])

    with pytest.raises(SendRefused) as ei:
        await send(
            org_id=scene.org, conversation_id=conv, body="promo",
            audit_id=audit, execution_token="t", meta_client=meta, message_class="marketing",
        )
    assert ei.value.code == "suppressed_contact"
    assert meta.calls == []

    # A transactional message to the same (marketing-)suppressed contact is allowed.
    audit2 = await scene.mint_audit(conv)
    outcome = await send(
        org_id=scene.org, conversation_id=conv, body="your order shipped",
        audit_id=audit2, execution_token="t", meta_client=meta, message_class="transactional",
    )
    assert outcome.sent is True and len(meta.calls) == 1


async def test_consent_unknown_marketing_blocked_transactional_allowed(scene: Scene) -> None:
    conv = await scene.conversation(consent="unknown")
    audit = await scene.mint_audit(conv)
    meta = FakeMeta([SendResult(ok=True, provider_message_id="wamid.T")])

    with pytest.raises(SendRefused) as ei:
        await send(
            org_id=scene.org, conversation_id=conv, body="promo",
            audit_id=audit, execution_token="t", meta_client=meta, message_class="marketing",
        )
    assert ei.value.code == "consent_missing"
    assert meta.calls == []

    audit2 = await scene.mint_audit(conv)
    outcome = await send(
        org_id=scene.org, conversation_id=conv, body="receipt",
        audit_id=audit2, execution_token="t", meta_client=meta, message_class="transactional",
    )
    assert outcome.sent is True


async def test_429_retry_after_is_honored(scene: Scene) -> None:
    conv = await scene.conversation()
    audit = await scene.mint_audit(conv)
    meta = FakeMeta([
        SendResult(ok=False, status_code=429, retry_after_s=1.5),
        SendResult(ok=True, provider_message_id="wamid.OK"),
    ])
    sleeper = RecordingSleeper()

    outcome = await send(
        org_id=scene.org, conversation_id=conv, body="x",
        audit_id=audit, execution_token="t", meta_client=meta, sleeper=sleeper,
    )
    assert outcome.sent is True
    assert len(meta.calls) == 2
    assert sleeper.delays == [1.5]  # honoured the server's Retry-After


async def test_5xx_retried_thrice_then_failed(scene: Scene) -> None:
    conv = await scene.conversation()
    audit = await scene.mint_audit(conv)
    meta = FakeMeta([SendResult(ok=False, status_code=500, error="boom")])
    sleeper = RecordingSleeper()

    outcome = await send(
        org_id=scene.org, conversation_id=conv, body="x",
        audit_id=audit, execution_token="t", meta_client=meta, sleeper=sleeper,
    )
    assert outcome.sent is False and outcome.retryable is True
    assert len(meta.calls) == 4  # 1 initial + 3 retries
    assert await _message_status(outcome.message_id) == ("failed", None)  # type: ignore[arg-type]
    assert "msg.failed.v1" in await _outbox_types(scene.org)


async def test_suppression_lookup_error_fails_closed() -> None:
    class BoomSession:
        async def execute(self, *a: object, **k: object) -> object:
            raise RuntimeError("db down")

    with pytest.raises(SendRefused) as ei:
        await send_mod._suppression_scopes(BoomSession(), uuid.uuid4())  # type: ignore[arg-type]
    assert ei.value.code == "suppressed_contact"
