"""Razorpay capture webhook + confirmation sweep (PAY3b) against real Postgres.

Proves the confirmation path: a bad signature is rejected (403); a valid capture is persisted once
(dedupe) and NEVER 5xx'd; the sweep maps the signed `notes` → transaction, marks it paid and drafts
the PAY3 `receipt.send` approval, idempotently; an unknown transaction is a no-op; and the operator
payment-link endpoint returns a simulated link and records the provider ref. Skips when DB is down.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg
import httpx
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.payments.reconcile import confirm_pending_razorpay
from core.tenancy.auth import issue_access_token

_SECRET = "whsec_pay3b_test"


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


def _sign(raw: bytes) -> str:
    return hmac.new(_SECRET.encode(), raw, hashlib.sha256).hexdigest()


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
    monkeypatch.setenv("GROWTH_OPERATOR_RAZORPAY_WEBHOOK_SECRET", _SECRET)
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
        await conn.execute(
            "DELETE FROM webhook_events WHERE provider='razorpay' AND "
            "payload->'payload'->'payment_link'->'entity'->'notes'->>'org_id' = $1", str(org))
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


def _tx_payload(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "store_name": "Ratna Store",
        "line_items": [{"description": "Growth plan — monthly", "amount_minor": 2_500_000}],
        "contact_email": "owner@ratna.example", "contact_phone": "+919000000000",
    }
    base.update(over)
    return base


async def _create_tx(scene: Scene) -> dict:
    r = await scene.client.post(
        f"/v1/admin/tenants/{scene.org}/transactions", headers=_op(scene.operator),
        json=_tx_payload())
    assert r.status_code == 201, r.text
    return r.json()


def _paid_event(org: uuid.UUID, tx_id: str) -> bytes:
    return json.dumps({
        "event": "payment_link.paid",
        "payload": {"payment_link": {"entity": {
            "id": "plink_test", "reference_id": "RATN-2608-001", "status": "paid",
            "notes": {"org_id": str(org), "tx_id": tx_id},
        }}},
    }).encode()


async def _post_webhook(scene: Scene, raw: bytes, *, sig: str, event_id: str) -> httpx.Response:
    return await scene.client.post(
        "/webhooks/razorpay", content=raw,
        headers={"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id,
                 "Content-Type": "application/json"})


async def _approval_count(org: uuid.UUID) -> int:
    conn = await asyncpg.connect(_dsn())
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM approvals WHERE org_id=$1 AND action_type='receipt.send'", org)
        return int(n)
    finally:
        await conn.close()


async def _tx_status(org: uuid.UUID, tx_id: str) -> str:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(
            "SELECT status FROM transactions WHERE id=$1 AND org_id=$2", uuid.UUID(tx_id), org)
    finally:
        await conn.close()


# ---- webhook ingress -------------------------------------------------------------------------

async def test_webhook_bad_signature_403(scene: Scene) -> None:
    raw = _paid_event(scene.org, str(uuid.uuid4()))
    r = await _post_webhook(scene, raw, sig="deadbeef", event_id="evt_bad")
    assert r.status_code == 403


async def test_webhook_persists_and_dedupes(scene: Scene) -> None:
    tx = await _create_tx(scene)
    raw = _paid_event(scene.org, tx["id"])
    sig = _sign(raw)
    r1 = await _post_webhook(scene, raw, sig=sig, event_id="evt_dupe")
    r2 = await _post_webhook(scene, raw, sig=sig, event_id="evt_dupe")  # Razorpay retry
    assert r1.status_code == 200 and r2.status_code == 200
    conn = await asyncpg.connect(_dsn())
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM webhook_events WHERE provider='razorpay' AND external_id=$1",
            "evt_dupe")
    finally:
        await conn.close()
    assert n == 1  # a retry is a single row


# ---- confirmation sweep ----------------------------------------------------------------------

async def test_reconcile_confirms_payment_and_drafts_receipt_approval(scene: Scene) -> None:
    tx = await _create_tx(scene)
    raw = _paid_event(scene.org, tx["id"])
    await _post_webhook(scene, raw, sig=_sign(raw), event_id="evt_confirm")

    drafted = await confirm_pending_razorpay()
    assert drafted >= 1
    assert await _tx_status(scene.org, tx["id"]) == "paid"   # payment confirmed
    assert await _approval_count(scene.org) == 1             # receipt approval drafted (not sent)


async def test_reconcile_is_idempotent(scene: Scene) -> None:
    tx = await _create_tx(scene)
    raw = _paid_event(scene.org, tx["id"])
    await _post_webhook(scene, raw, sig=_sign(raw), event_id="evt_idem")

    await confirm_pending_razorpay()
    await confirm_pending_razorpay()  # processed row isn't re-swept
    assert await _approval_count(scene.org) == 1             # never a second approval
    assert await _tx_status(scene.org, tx["id"]) == "paid"


async def test_reconcile_unknown_tx_is_noop(scene: Scene) -> None:
    raw = _paid_event(scene.org, str(uuid.uuid4()))          # a tx that doesn't exist
    await _post_webhook(scene, raw, sig=_sign(raw), event_id="evt_unknown")
    drafted = await confirm_pending_razorpay()
    assert drafted == 0
    assert await _approval_count(scene.org) == 0


# ---- payment-link endpoint -------------------------------------------------------------------

async def test_payment_link_endpoint_creates_simulated_link(scene: Scene) -> None:
    tx = await _create_tx(scene)
    r = await scene.client.post(
        f"/v1/admin/tenants/{scene.org}/transactions/{tx['id']}/payment-link",
        headers=_op(scene.operator))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["simulated"] is True and body["provider"] == "razorpay"
    assert (body["provider_ref"] or "").startswith("plink_SIM")
    assert body["pay_url"]
    conn = await asyncpg.connect(_dsn())
    try:
        ref = await conn.fetchval(
            "SELECT provider_ref FROM transactions WHERE id=$1", uuid.UUID(tx["id"]))
    finally:
        await conn.close()
    assert ref == body["provider_ref"]                       # persisted for webhook mapping


async def test_payment_link_409_when_not_created(scene: Scene) -> None:
    tx = await _create_tx(scene)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "UPDATE transactions SET status='receipted' WHERE id=$1", uuid.UUID(tx["id"]))
    finally:
        await conn.close()
    r = await scene.client.post(
        f"/v1/admin/tenants/{scene.org}/transactions/{tx['id']}/payment-link",
        headers=_op(scene.operator))
    assert r.status_code == 409
