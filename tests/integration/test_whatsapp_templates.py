"""WhatsApp template management (MVP-035) against real Postgres under app_rw.

Covers the template lifecycle (draft → submit → pending → approved/rejected via Meta status),
the send gate (a non-approved template is refused, naming it, with no Meta call), the status
webhook drainer (resolves org by WABA id; the normalizer leaves these alone), pack-manifest
seeding, and cross-org isolation. Meta is faked/simulated — no network I/O. Skips when the DB
is unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
import yaml
from sqlalchemy import text

from core.audit.writer import AuditEntry, write
from core.channels.whatsapp import normalizer, templates
from core.channels.whatsapp.connect import templates as list_templates_endpoint
from core.channels.whatsapp.credentials import store_credentials
from core.channels.whatsapp.meta_client import SendResult
from core.channels.whatsapp.send import send
from core.channels.whatsapp.templates import TemplateNotSendable
from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.deps import CurrentAuth
from core.tenancy.middleware import org_scoped_session


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        has_col = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='message_templates' AND column_name='category'"
        )
        has_fn = await conn.fetchval(
            "SELECT 1 FROM pg_proc WHERE proname='resolve_channel_by_waba'"
        )
        return bool(has_col) and bool(has_fn)
    finally:
        await conn.close()


class FakeMeta:
    """Simulated Meta client tracking which send path was used."""

    def __init__(self, result: SendResult | None = None) -> None:
        self.result = result or SendResult(ok=True, provider_message_id="wamid.T")
        self.text_calls = 0
        self.template_calls = 0

    @property
    def simulated(self) -> bool:
        return True

    async def send_text(self, *a: object, **k: object) -> SendResult:
        self.text_calls += 1
        return self.result

    async def send_template(self, *a: object, **k: object) -> SendResult:
        self.template_calls += 1
        return self.result


@pytest.fixture()
async def scene() -> AsyncIterator[dict]:
    if not await _db_ready():
        pytest.skip("Postgres/message_templates_meta (83efabba79ee) not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    org = uuid.uuid4()
    pnid, waba = f"pn-{org.hex[:8]}", f"waba-{org.hex[:8]}"
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'T')", org)
        channel_id = await conn.fetchval(
            "INSERT INTO channels (org_id, type, external_id, credentials_ref, waba_id) "
            "VALUES ($1,'whatsapp',$2,'channel_credentials',$3) RETURNING id",
            org, pnid, waba,
        )
    finally:
        await conn.close()
    async with org_scoped_session(org) as s:
        await store_credentials(
            s, org_id=org, channel_id=channel_id,
            credentials={"waba_id": waba, "phone_number_id": pnid, "access_token": "tok"},
        )
    yield {"org": org, "channel_id": channel_id, "waba": waba, "pnid": pnid}
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id=$1", org)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM event_outbox WHERE org_id=$1", org)
        await conn.execute("DELETE FROM webhook_events WHERE provider='whatsapp'")
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def _conversation(org: uuid.UUID, channel_id: uuid.UUID) -> uuid.UUID:
    conn = await asyncpg.connect(_dsn())
    try:
        contact = await conn.fetchval(
            "INSERT INTO contacts (org_id, phone, consent_status) VALUES ($1,$2,'opted_in') "
            "RETURNING id",
            org, f"+1555{uuid.uuid4().int % 10_000_000:07d}",
        )
        return await conn.fetchval(
            "INSERT INTO conversations (org_id, contact_id, channel_id) VALUES ($1,$2,$3) "
            "RETURNING id",
            org, contact, channel_id,
        )
    finally:
        await conn.close()


async def _mint_audit(org: uuid.UUID, conv: uuid.UUID) -> uuid.UUID:
    async with org_scoped_session(org) as s:
        aid = await write(
            s, AuditEntry(org_id=org, actor_type="user", actor_id=str(uuid.uuid4()),
                          action="msg.send", resource=str(conv), payload={}),
        )
    return aid.id


async def test_lifecycle_draft_submit_approve_reject(scene: dict) -> None:
    org, waba = scene["org"], scene["waba"]
    async with org_scoped_session(org) as s:
        await templates.upsert_template(
            s, org, template_key="festival", language="en",
            body="Happy {{1}}!", category="MARKETING", namespace="jewelry_v2",
        )
        # Submit (simulated) → pending + a provider id.
        result = await templates.submit_template(
            s, org, template_key="festival", language="en", waba_id=waba, access_token="tok",
        )
        assert result.ok and result.provider_template_id
        tpl = await templates.get_template(s, org, "festival", "en")
        assert tpl is not None and tpl["provider_status"] == "pending"

        # Meta approves.
        assert await templates.apply_status_update(
            s, org, template_key="festival", language="en", event="APPROVED",
        ) is True
        approved = await templates.get_template(s, org, "festival", "en")
        assert approved is not None and approved["provider_status"] == "approved"

        # Meta later rejects (with a reason).
        await templates.apply_status_update(
            s, org, template_key="festival", language="en", event="REJECTED",
            reason="promotional content not allowed",
        )
        rej = await templates.get_template(s, org, "festival", "en")
        assert rej["provider_status"] == "rejected"
        assert rej["provider_reason"] == "promotional content not allowed"


async def test_gate_blocks_non_approved_naming_template(scene: dict) -> None:
    org = scene["org"]
    async with org_scoped_session(org) as s:
        await templates.upsert_template(
            s, org, template_key="holding", language="en", body="hold {{1}}", category="UTILITY",
        )
        await templates.apply_status_update(
            s, org, template_key="holding", language="en", event="REJECTED", reason="bad format",
        )
        with pytest.raises(TemplateNotSendable) as ei:
            await templates.assert_template_sendable(s, org, "holding", "en")
    assert ei.value.template_key == "holding" and ei.value.status == "rejected"
    assert "holding" in str(ei.value) and "bad format" in str(ei.value)


async def test_send_with_rejected_template_refused_no_meta_call(scene: dict) -> None:
    org, channel_id = scene["org"], scene["channel_id"]
    conv = await _conversation(org, channel_id)
    audit = await _mint_audit(org, conv)
    async with org_scoped_session(org) as s:
        await templates.upsert_template(
            s, org, template_key="reactivation", language="en", body="hi {{1}}",
            category="MARKETING",
        )
        await templates.apply_status_update(
            s, org, template_key="reactivation", language="en", event="REJECTED",
        )
    meta = FakeMeta()
    with pytest.raises(TemplateNotSendable):
        await send(
            org_id=org, conversation_id=conv, body="hi", audit_id=audit,
            execution_token="t", template=("reactivation", "en"), meta_client=meta,
        )
    assert meta.template_calls == 0 and meta.text_calls == 0  # never hit the wire


async def test_send_with_approved_template_uses_template_path(scene: dict) -> None:
    org, channel_id = scene["org"], scene["channel_id"]
    conv = await _conversation(org, channel_id)
    audit = await _mint_audit(org, conv)
    async with org_scoped_session(org) as s:
        await templates.upsert_template(
            s, org, template_key="visit_reminder", language="en", body="visit {{1}}",
            category="UTILITY",
        )
        await templates.apply_status_update(
            s, org, template_key="visit_reminder", language="en", event="APPROVED",
        )
    meta = FakeMeta(SendResult(ok=True, provider_message_id="wamid.TPL"))
    outcome = await send(
        org_id=org, conversation_id=conv, body="visit today", audit_id=audit,
        execution_token="t", template=("visit_reminder", "en"), message_class="transactional",
        meta_client=meta,
    )
    assert outcome.sent is True
    assert meta.template_calls == 1 and meta.text_calls == 0  # template path, not freeform


async def test_status_webhook_drainer_and_normalizer_skips(scene: dict) -> None:
    org, waba = scene["org"], scene["waba"]
    async with org_scoped_session(org) as s:
        await templates.upsert_template(
            s, org, template_key="approval", language="en", body="approve {{1}}",
            category="UTILITY",
        )
    payload = json.dumps({"entry": [{"id": waba, "changes": [{
        "field": "message_template_status_update",
        "value": {"message_template_id": "mtpl.9", "message_template_name": "approval",
                  "message_template_language": "en", "event": "APPROVED"},
    }]}]})
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "INSERT INTO webhook_events (provider, external_id, payload) "
            "VALUES ('whatsapp', $1, $2::jsonb)",
            f"evt-{uuid.uuid4().hex}", payload,
        )
    finally:
        await conn.close()

    # The message normalizer must NOT touch a template-status webhook.
    assert await normalizer.normalize_pending() == 0
    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval(
            "SELECT processed_at FROM webhook_events WHERE payload->'entry'->0->>'id'=$1", waba
        ) is None
    finally:
        await conn.close()

    # The template drainer resolves org by WABA id and applies the status.
    assert await templates.process_template_status_pending() >= 1
    async with org_scoped_session(org) as s:
        tpl = await templates.get_template(s, org, "approval", "en")
    assert tpl is not None and tpl["provider_status"] == "approved"
    assert tpl["provider_template_id"] == "mtpl.9"


async def test_seed_from_manifest_and_isolation(scene: dict) -> None:
    org = scene["org"]
    manifest = [
        {"template_key": "a", "language": "en", "body": "a {{1}}", "category": "UTILITY"},
        {"template_key": "b", "language": "en", "body": "b {{1}}", "category": "MARKETING"},
    ]
    async with org_scoped_session(org) as s:
        ids = await templates.seed_from_manifest(s, org, manifest, namespace="ns1")
        assert len(ids) == 2
        listed = await templates.list_templates(s, org)
    assert {t["template_key"] for t in listed} == {"a", "b"}
    assert all(t["provider_status"] == "draft" for t in listed)

    # Another org with the same key is untouched by this org's status update.
    other = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'O2')", other)
    finally:
        await conn.close()
    try:
        async with org_scoped_session(other) as s:
            await templates.upsert_template(
                s, other, template_key="a", language="en", body="a", category="UTILITY",
            )
        async with org_scoped_session(org) as s:
            await templates.apply_status_update(
                s, org, template_key="a", language="en", event="APPROVED",
            )
        async with org_scoped_session(other) as s:
            other_a = await templates.get_template(s, other, "a", "en")
        assert other_a is not None and other_a["provider_status"] == "draft"  # not approved
    finally:
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute("DELETE FROM organizations WHERE id=$1", other)
        finally:
            await conn.close()


async def test_list_endpoint_returns_org_templates(scene: dict) -> None:
    org = scene["org"]
    async with org_scoped_session(org) as s:
        await templates.upsert_template(
            s, org, template_key="festival", language="en", body="hi {{1}}", category="MARKETING",
        )
    auth = CurrentAuth(user_id=uuid.uuid4(), org_id=org, roles=["owner"])
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        await s.execute(text("SELECT set_config('app.org_id', :v, true)"), {"v": str(org)})
        items = await list_templates_endpoint(current=auth, session=s)
    assert [i.template_key for i in items] == ["festival"]
    assert items[0].provider_status == "draft"


def test_jewelry_pack_seed_manifest_is_valid() -> None:
    data = yaml.safe_load(
        Path("verticals/jewelry/templates/whatsapp.yaml").read_text()
    )
    assert data["namespace"] == "jewelry_v2"
    keys = {t["template_key"] for t in data["templates"]}
    assert keys == {"approval", "reactivation", "festival", "visit_reminder", "holding"}
    for t in data["templates"]:
        assert t["language"] and t["category"] in {"MARKETING", "UTILITY", "AUTHENTICATION"}
        assert t["body"].strip()
