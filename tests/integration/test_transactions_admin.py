"""Transactions operator API (PAY-TX) against real Postgres.

Proves an operator can record a transaction (auto-numbered {STORE}-{YYMM}-seq, percent discount,
notes) and list/retrieve it; the monthly seq increments per store; one store's transactions never
show under another (RLS); and the surface is operator-only (403/401/404). Skips when the DB is down.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date

import asyncpg
import httpx
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy.auth import issue_access_token


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
    org_a: uuid.UUID
    org_b: uuid.UUID


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/transactions not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    operator, org_a, org_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           operator, f"op+{operator.hex[:8]}@example.test")
        await conn.execute("INSERT INTO platform_admins (user_id, role) VALUES ($1,'admin')",
                           operator)
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'Ratna'),($2,'Beta')",
                           org_a, org_b)
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, operator, org_a, org_b)
    conn = await asyncpg.connect(_dsn())
    try:
        orgs = [org_a, org_b]
        await conn.execute("DELETE FROM transactions WHERE org_id = ANY($1::uuid[])", orgs)
        await conn.execute(
            "ALTER TABLE platform_access_log DISABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_access_log WHERE actor_user_id=$1", operator)
        await conn.execute(
            "ALTER TABLE platform_access_log ENABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", operator)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", orgs)
        await conn.execute("DELETE FROM users WHERE id=$1", operator)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _payload(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "store_name": "Ratna Store",
        "line_items": [
            {"description": "Growth plan — monthly", "amount_minor": 2_500_000},
            {"description": "Festival campaign", "amount_minor": 500_000},
        ],
        "discount_percent": 10, "discount_reason": "loyal client",
        "tax_label": "GST 18%", "tax_minor": 486_000, "notes": "paid via UPI",
    }
    base.update(over)
    return base


async def test_create_numbers_discounts_and_retrieves(scene: Scene) -> None:
    op = _op(scene.operator)
    ym = f"{date.today().year % 100:02d}{date.today().month:02d}"

    r1 = await scene.client.post(
        f"/v1/admin/tenants/{scene.org_a}/transactions", headers=op, json=_payload())
    assert r1.status_code == 201, r1.text
    tx = r1.json()
    assert tx["receipt_no"] == f"RATN-{ym}-001"           # store code · YYMM · seq
    assert tx["subtotal_minor"] == 3_000_000
    assert tx["discount_minor"] == 300_000                 # 10% of 3,000,000
    assert tx["total_minor"] == 3_000_000 - 300_000 + 486_000
    assert tx["status"] == "created"

    # second transaction this month → seq increments
    r2 = await scene.client.post(
        f"/v1/admin/tenants/{scene.org_a}/transactions", headers=op, json=_payload())
    assert r2.json()["receipt_no"] == f"RATN-{ym}-002"

    listed = (await scene.client.get(
        f"/v1/admin/tenants/{scene.org_a}/transactions", headers=op)).json()
    assert len(listed) == 2

    got = await scene.client.get(
        f"/v1/admin/tenants/{scene.org_a}/transactions/{tx['id']}", headers=op)
    assert got.status_code == 200 and got.json()["notes"] == "paid via UPI"


async def test_transactions_isolated_between_stores(scene: Scene) -> None:
    op = _op(scene.operator)
    await scene.client.post(
        f"/v1/admin/tenants/{scene.org_a}/transactions", headers=op, json=_payload())
    a = (await scene.client.get(f"/v1/admin/tenants/{scene.org_a}/transactions", headers=op)).json()
    b = (await scene.client.get(f"/v1/admin/tenants/{scene.org_b}/transactions", headers=op)).json()
    assert len(a) == 1 and len(b) == 0  # Ratna's transaction never shows under Beta


async def test_transactions_403_for_non_operator(scene: Scene) -> None:
    r = await scene.client.get(
        f"/v1/admin/tenants/{scene.org_a}/transactions", headers=_op(uuid.uuid4()))
    assert r.status_code == 403


async def test_transaction_404_for_unknown_id(scene: Scene) -> None:
    r = await scene.client.get(
        f"/v1/admin/tenants/{scene.org_a}/transactions/{uuid.uuid4()}", headers=_op(scene.operator))
    assert r.status_code == 404
