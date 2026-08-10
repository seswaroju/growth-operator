"""Approval-gated receipt delivery (PAY3) against real Postgres.

Proves the gate: requesting a receipt marks the transaction paid and *drafts an approval* — nothing
sends yet (202 pending_approval). Delivery (the consumer's job) renders + sends via the gated
clients (simulated email/WhatsApp in tests), sets the transaction `receipted`, and is idempotent so
a redelivered `approval.resolved` never double-sends. A rejected approval sends nothing. Skips when
the DB is down.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg
import httpx
import pytest
from sqlalchemy import text

from core.channels.whatsapp.credentials import store_credentials
from core.common import db as dbmod
from core.common.config import get_settings
from core.payments.delivery import deliver_receipt
from core.payments.receipt_consumer import on_receipt_approval_resolved
from core.tenancy.auth import issue_access_token
from core.tenancy.middleware import org_scoped_session


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.transactions')"))
    finally:
        await conn.close()


def _op(user: uuid.UUID) -> dict[str, str]:
    token = issue_access_token(
        sub=str(user), secret=get_settings().jwt_secret, org_id=None, roles=[])
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Scene:
    client: httpx.AsyncClient
    operator: uuid.UUID
    org: uuid.UUID


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/transactions not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    operator, org = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           operator, f"op+{operator.hex[:8]}@example.test")
        await conn.execute("INSERT INTO platform_admins (user_id, role) VALUES ($1,'admin')",
                           operator)
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Ratna')", org)
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, operator, org)
    conn = await asyncpg.connect(_dsn())
    try:
        # org delete CASCADEs approvals / event_outbox / channels / channel_credentials.
        await conn.execute("DELETE FROM transactions WHERE org_id=$1", org)
        await conn.execute(
            "ALTER TABLE platform_access_log DISABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_access_log WHERE actor_user_id=$1", operator)
        await conn.execute(
            "ALTER TABLE platform_access_log ENABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", operator)
        await conn.execute("DELETE FROM organizations WHERE id=$1", org)
        await conn.execute("DELETE FROM users WHERE id=$1", operator)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _payload(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "store_name": "Ratna Store",
        "line_items": [{"description": "Growth plan — monthly", "amount_minor": 2_500_000}],
        "discount_percent": 10, "discount_reason": "loyal client",
        "tax_label": "GST 18%", "tax_minor": 450_000, "notes": "paid via UPI",
        "contact_email": "owner@ratna.example", "contact_phone": "+919000000000",
    }
    base.update(over)
    return base


async def _create_tx(scene: Scene, **over: object) -> dict:
    r = await scene.client.post(
        f"/v1/admin/tenants/{scene.org}/transactions", headers=_op(scene.operator),
        json=_payload(**over))
    assert r.status_code == 201, r.text
    return r.json()


async def _tx_status(org: uuid.UUID, tx_id: str) -> str:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(
            "SELECT status FROM transactions WHERE id=$1 AND org_id=$2", uuid.UUID(tx_id), org)
    finally:
        await conn.close()


# ---- request-receipt (the gate) -------------------------------------------------------------

async def test_request_receipt_marks_paid_and_queues_approval(scene: Scene) -> None:
    tx = await _create_tx(scene)
    r = await scene.client.post(
        f"/v1/admin/tenants/{scene.org}/transactions/{tx['id']}/request-receipt",
        headers=_op(scene.operator))
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "pending_approval"
    assert body["receipt_no"] == tx["receipt_no"]

    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval(
            "SELECT status FROM transactions WHERE id=$1", uuid.UUID(tx["id"])) == "paid"
        row = await conn.fetchrow(
            "SELECT action_type, status, payload->>'transaction_id' AS txid "
            "FROM approvals WHERE id=$1", uuid.UUID(body["approval_id"]))
        assert row["action_type"] == "receipt.send"
        assert row["status"] == "pending"          # nothing sent — awaits owner approval
        assert row["txid"] == tx["id"]
    finally:
        await conn.close()


async def test_request_receipt_409_when_already_receipted(scene: Scene) -> None:
    tx = await _create_tx(scene)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "UPDATE transactions SET status='receipted' WHERE id=$1", uuid.UUID(tx["id"]))
    finally:
        await conn.close()
    r = await scene.client.post(
        f"/v1/admin/tenants/{scene.org}/transactions/{tx['id']}/request-receipt",
        headers=_op(scene.operator))
    assert r.status_code == 409


async def test_request_receipt_404_for_unknown_tx(scene: Scene) -> None:
    r = await scene.client.post(
        f"/v1/admin/tenants/{scene.org}/transactions/{uuid.uuid4()}/request-receipt",
        headers=_op(scene.operator))
    assert r.status_code == 404


async def test_request_receipt_403_for_non_operator(scene: Scene) -> None:
    tx = await _create_tx(scene)
    r = await scene.client.post(
        f"/v1/admin/tenants/{scene.org}/transactions/{tx['id']}/request-receipt",
        headers=_op(uuid.uuid4()))
    assert r.status_code == 403


# ---- deliver_receipt (idempotent, gated send) ----------------------------------------------

async def test_deliver_receipt_sends_email_and_is_idempotent(scene: Scene) -> None:
    tx = await _create_tx(scene, contact_phone=None)   # email only, no WhatsApp number
    async with org_scoped_session(scene.org) as s:
        first = await deliver_receipt(s, scene.org, uuid.UUID(tx["id"]))
    assert first.delivered and first.sent_email and not first.sent_whatsapp
    assert not first.already_sent
    assert await _tx_status(scene.org, tx["id"]) == "receipted"

    async with org_scoped_session(scene.org) as s:
        second = await deliver_receipt(s, scene.org, uuid.UUID(tx["id"]))
    assert second.already_sent and second.delivered      # idempotent — no second send
    assert await _tx_status(scene.org, tx["id"]) == "receipted"


async def test_deliver_receipt_skips_whatsapp_when_no_channel(scene: Scene) -> None:
    tx = await _create_tx(scene)                         # has a phone, but no connected channel
    async with org_scoped_session(scene.org) as s:
        res = await deliver_receipt(s, scene.org, uuid.UUID(tx["id"]))
    assert res.sent_email and not res.sent_whatsapp      # graceful skip


async def test_deliver_receipt_sends_whatsapp_when_channel_connected(scene: Scene) -> None:
    tx = await _create_tx(scene)
    async with org_scoped_session(scene.org) as s:
        channel_id = (await s.execute(
            text("INSERT INTO channels (org_id, type, external_id, credentials_ref, status) "
                 "VALUES (:o,'whatsapp',:eid,'vault://test','active') RETURNING id"),
            {"o": scene.org, "eid": f"pn-{uuid.uuid4().hex[:8]}"})).scalar_one()
        await store_credentials(
            s, org_id=scene.org, channel_id=channel_id,
            credentials={"phone_number_id": "PN123", "access_token": "tok", "waba_id": "WABA1"})
    async with org_scoped_session(scene.org) as s:
        res = await deliver_receipt(s, scene.org, uuid.UUID(tx["id"]))
    assert res.sent_email and res.sent_whatsapp          # simulated MetaClient returns ok


async def test_deliver_receipt_missing_tx_is_no_op(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        res = await deliver_receipt(s, scene.org, uuid.uuid4())
    assert not res.delivered and not res.already_sent


# ---- consumer (approval.resolved → deliver, only on approve) --------------------------------

async def test_consumer_delivers_only_on_approved(scene: Scene) -> None:
    tx = await _create_tx(scene, contact_phone=None)
    req = await scene.client.post(
        f"/v1/admin/tenants/{scene.org}/transactions/{tx['id']}/request-receipt",
        headers=_op(scene.operator))
    approval_id = req.json()["approval_id"]

    approved = {"subject": str(scene.org),
                "data": {"approval_id": approval_id, "decision": "approved"}}
    await on_receipt_approval_resolved(approved)
    assert await _tx_status(scene.org, tx["id"]) == "receipted"


async def test_consumer_ignores_rejected(scene: Scene) -> None:
    tx = await _create_tx(scene, contact_phone=None)
    req = await scene.client.post(
        f"/v1/admin/tenants/{scene.org}/transactions/{tx['id']}/request-receipt",
        headers=_op(scene.operator))
    approval_id = req.json()["approval_id"]

    rejected = {"subject": str(scene.org),
                "data": {"approval_id": approval_id, "decision": "rejected"}}
    await on_receipt_approval_resolved(rejected)
    assert await _tx_status(scene.org, tx["id"]) == "paid"   # request marked it paid; not receipted
