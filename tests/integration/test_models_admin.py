"""Operator LLM-model config (CP-5) — `/v1/admin/tenants/{org_id}/models`.

The GO operator sets, per store + per agent-task, which provider+model the runtime uses. Default is
Claude 3.5 Sonnet; an override is stored in `org_model_routes` and validated against the model
catalog. Operator-gated. Rigorous corner-case coverage. Skips when the DB is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg
import httpx
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.runtime.model_registry import current_models, is_retired
from core.tenancy.auth import issue_access_token


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.org_model_routes')"))
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
    tag: str


@pytest.fixture()
async def scene(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/org_model_routes not ready")
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "true")
    # PILOT-1B: a model is only selectable when its provider is actually callable, so the operator
    # surface is exercised with credentials present — the unavailable case has its own test.
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_KEY_OPENAI", "sk-test-openai")
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_KEY_ANTHROPIC", "sk-test-anthropic")
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_KEY_DEEPSEEK", "sk-test-deepseek")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    operator, org = uuid.uuid4(), uuid.uuid4()
    tag = operator.hex[:8]
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO users (id, email) VALUES ($1,$2)",
                           operator, f"op+{tag}@example.test")
        await conn.execute("INSERT INTO platform_admins (user_id, role) VALUES ($1,'admin')",
                           operator)
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,$2)",
                           org, f"ModelStore-{tag}-A")
    finally:
        await conn.close()
    from core.api.main import app
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Scene(client, operator, org, tag)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("DELETE FROM organizations WHERE name LIKE $1", f"ModelStore-{tag}%")
        await conn.execute(
            "ALTER TABLE platform_access_log DISABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_access_log WHERE actor_user_id=$1", operator)
        await conn.execute(
            "ALTER TABLE platform_access_log ENABLE TRIGGER trg_platform_access_log_immutable")
        await conn.execute("DELETE FROM platform_admins WHERE user_id=$1", operator)
        await conn.execute("DELETE FROM users WHERE id=$1", operator)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


def _models_url(org: uuid.UUID) -> str:
    return f"/v1/admin/tenants/{org}/models"


async def test_catalog_lists_choices_and_default(scene: Scene) -> None:
    r = await scene.client.get("/v1/admin/tenants/model-catalog", headers=_op(scene.operator))
    assert r.status_code == 200, r.text
    body = r.json()
    pairs = {(m["provider"], m["model"]) for m in body["models"]}
    assert ("anthropic", "claude-sonnet-5") in pairs and ("openai", "gpt-5.6-sol") in pairs
    assert body["default_provider"] == "anthropic"
    # Asserted as a PROPERTY, not an id. The default is an explicit placeholder until live
    # evaluation picks one, so pinning a model here would fail every time that decision is
    # revisited while still not catching the thing that matters: that it is callable at all.
    assert not is_retired(body["default_model"])
    assert body["default_model"] in {m.model for m in current_models()}
    node_keys = {n["node_key"] for n in body["nodes"]}
    assert {"default", "converse", "campaign", "classify"} <= node_keys


async def test_effective_config_defaults_when_no_override(scene: Scene) -> None:
    r = await scene.client.get(_models_url(scene.org), headers=_op(scene.operator))
    assert r.status_code == 200, r.text
    by_key = {i["node_key"]: i for i in r.json()}
    # every tunable node reads the GLOBAL default (per-node), none is an override
    assert by_key["default"]["provider"] == "anthropic"
    assert by_key["default"]["model"] == "claude-sonnet-5"
    assert by_key["classify"]["model"] == "claude-haiku-4-5-20251001"  # its own seeded default
    assert all(i["is_override"] is False for i in r.json())


async def test_set_override_then_effective_reflects_it(scene: Scene) -> None:
    op = _op(scene.operator)
    put = await scene.client.put(
        f"{_models_url(scene.org)}/converse", headers=op,
        json={"provider": "openai", "model": "gpt-5.6-sol"})
    assert put.status_code == 200, put.text
    item = put.json()
    assert item["provider"] == "openai" and item["model"] == "gpt-5.6-sol"
    assert item["is_override"] is True
    assert item["default_provider"] == "anthropic"
    assert not is_retired(item["default_model"])
    assert item["default_model"] in {m.model for m in current_models()}
    # persisted + reflected in the effective list
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT provider, model FROM org_model_routes WHERE org_id=$1 AND node_key='converse'",
            scene.org)
        assert row is not None and row["provider"] == "openai" and row["model"] == "gpt-5.6-sol"
    finally:
        await conn.close()
    lst = await scene.client.get(_models_url(scene.org), headers=op)
    conv = next(i for i in lst.json() if i["node_key"] == "converse")
    assert conv["is_override"] is True and conv["model"] == "gpt-5.6-sol"


async def test_clear_override_reverts_to_default(scene: Scene) -> None:
    op = _op(scene.operator)
    await scene.client.put(
        f"{_models_url(scene.org)}/converse", headers=op,
        json={"provider": "openai", "model": "gpt-5.6-sol"})
    d = await scene.client.delete(f"{_models_url(scene.org)}/converse", headers=op)
    assert d.status_code == 204
    conn = await asyncpg.connect(_dsn())
    try:
        assert await conn.fetchval(
            "SELECT count(*) FROM org_model_routes WHERE org_id=$1", scene.org) == 0
    finally:
        await conn.close()
    conv = next(
        i for i in (await scene.client.get(_models_url(scene.org), headers=op)).json()
        if i["node_key"] == "converse")
    assert conv["is_override"] is False and conv["model"] == "claude-sonnet-5"


async def test_unknown_node_key_is_404(scene: Scene) -> None:
    r = await scene.client.put(
        f"{_models_url(scene.org)}/not_a_task", headers=_op(scene.operator),
        json={"provider": "openai", "model": "gpt-5.6-sol"})
    assert r.status_code == 404


async def test_unknown_model_is_422(scene: Scene) -> None:
    r = await scene.client.put(
        f"{_models_url(scene.org)}/converse", headers=_op(scene.operator),
        json={"provider": "anthropic", "model": "claude-99-ultra"})
    assert r.status_code == 422
    conn = await asyncpg.connect(_dsn())
    try:  # nothing written
        assert await conn.fetchval(
            "SELECT count(*) FROM org_model_routes WHERE org_id=$1", scene.org) == 0
    finally:
        await conn.close()


async def test_override_is_org_scoped(scene: Scene) -> None:
    op = _op(scene.operator)
    await scene.client.put(
        f"{_models_url(scene.org)}/converse", headers=op,
        json={"provider": "openai", "model": "gpt-5.6-sol"})
    other = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,$2)",
                           other, f"ModelStore-{scene.tag}-B")
    finally:
        await conn.close()
    # org B's effective config is unaffected — still the default
    conv = next(
        i for i in (await scene.client.get(_models_url(other), headers=op)).json()
        if i["node_key"] == "converse")
    assert conv["is_override"] is False and conv["model"] == "claude-sonnet-5"


async def test_non_operator_is_403(scene: Scene) -> None:
    r = await scene.client.get(_models_url(scene.org), headers=_op(uuid.uuid4()))
    assert r.status_code == 403


async def test_plane_disabled_is_404(scene: Scene, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_ADMIN_PLANE_ENABLED", "false")
    r = await scene.client.get(_models_url(scene.org), headers=_op(scene.operator))
    assert r.status_code == 404
