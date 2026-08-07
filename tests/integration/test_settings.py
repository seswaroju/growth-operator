"""Tenant settings service against real Postgres under app_rw (MVP-021).

Covers the four-layer precedence + provenance, tighten-only autonomy, point-in-time
resolve_at, and that a write appends a version and a settings.changed audit entry.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.tenancy import settings as svc
from core.tenancy.settings import SettingSource


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.tenant_settings')"))
    finally:
        await conn.close()


@pytest.fixture()
async def org() -> AsyncIterator[uuid.UUID]:
    if not await _db_ready():
        pytest.skip("Postgres/migration 009 not ready")
    # Rebuild the app engine on THIS test's event loop (avoid a pool bound to a closed loop).
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    o = uuid.uuid4()
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1, 'S')", o)
    finally:
        await conn.close()
    yield o
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM audit_log WHERE org_id = $1", o)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER trg_audit_log_immutable")
        await conn.execute("DELETE FROM organizations WHERE id = $1", o)  # cascades installs
        await conn.execute("DELETE FROM feature_flags WHERE key = 'reply.tone'")
        # Drop orphaned test packs (global table; installations already cascaded away).
        await conn.execute(
            "DELETE FROM packs WHERE id NOT IN (SELECT pack_id FROM pack_installations)"
        )
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_precedence_flag_over_tenant_over_pack_over_platform(org: uuid.UUID) -> None:
    factory = dbmod.get_sessionmaker()
    conn = await asyncpg.connect(_dsn())
    try:
        # platform default only (no rows) → PLATFORM
        async with factory() as s:
            r = await svc.resolve(s, org, "reply.tone")
        assert (r.value, r.source) == ("warm", SettingSource.PLATFORM)

        # add an active pack whose manifest sets a default → PACK
        pack_id = await conn.fetchval(
            "INSERT INTO packs (slug, version, platform_api, manifest, bundle_uri, signature) "
            "VALUES ($1,'1','1', $2::jsonb, 'u', 's') RETURNING id",
            f"jw-{org.hex[:8]}", '{"config_defaults": {"reply.tone": "elegant"}}',
        )
        await conn.execute(
            "INSERT INTO pack_installations (org_id, pack_id, status) VALUES ($1,$2,'active')",
            org, pack_id,
        )
        async with factory() as s:
            r = await svc.resolve(s, org, "reply.tone")
        assert (r.value, r.source) == ("elegant", SettingSource.PACK)

        # tenant setting overrides pack → TENANT
        async with factory() as s:
            await svc.write_setting(s, org_id=org, key="reply.tone", value="cordial")
            await s.commit()
        async with factory() as s:
            r = await svc.resolve(s, org, "reply.tone")
        assert (r.value, r.source, r.version) == ("cordial", SettingSource.TENANT, 1)

        # config flag rule overrides everything → FLAG
        flag_id = await conn.fetchval(
            "INSERT INTO feature_flags (key, flag_type, default_value) "
            "VALUES ('reply.tone','config','\"x\"') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO flag_rules (flag_id, scope, scope_ref, value) "
            "VALUES ($1,'tenant',$2,'\"flagged\"')",
            flag_id, str(org),
        )
        async with factory() as s:
            r = await svc.resolve(s, org, "reply.tone")
        assert (r.value, r.source) == ("flagged", SettingSource.FLAG)
    finally:
        await conn.close()


async def test_autonomy_is_free_dial(org: uuid.UUID) -> None:
    # DECISIONS 2026-08-06: the owner free-dials autonomy — loosening no longer needs an earned
    # trust threshold (the tier-4 floor is enforced in the engine, not here). Both directions work.
    factory = dbmod.get_sessionmaker()
    async with factory() as s:  # default "auto" → tighten to draft_only
        v1 = await svc.write_setting(s, org_id=org, key="autonomy.messaging", value="draft_only")
        await s.commit()
    async with factory() as s:  # loosen back to auto — previously a TightenOnlyViolation, now OK
        v2 = await svc.write_setting(s, org_id=org, key="autonomy.messaging", value="auto")
        await s.commit()
    assert (v1, v2) == (1, 2)
    async with factory() as s:
        r = await svc.resolve(s, org, "autonomy.messaging")
    assert (r.value, r.source) == ("auto", SettingSource.TENANT)


async def test_resolve_at_walks_history(org: uuid.UUID) -> None:
    conn = await asyncpg.connect(_dsn())
    t1 = datetime.now(UTC) - timedelta(hours=2)
    t2 = datetime.now(UTC) - timedelta(hours=1)
    try:
        for value, version, ts in (("warm", 1, t1), ("elegant", 2, t2)):
            await conn.execute(
                "INSERT INTO tenant_settings (org_id, key, value, version, updated_at) "
                "VALUES ($1,'reply.tone',$2::jsonb,$3,$4)",
                org, f'"{value}"', version, ts,
            )
    finally:
        await conn.close()

    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        at_mid = await svc.resolve_at(s, org, "reply.tone", t1 + timedelta(minutes=30))
        at_now = await svc.resolve_at(s, org, "reply.tone", datetime.now(UTC))
    assert at_mid.value == "warm" and at_mid.version == 1  # only v1 existed then
    assert at_now.value == "elegant" and at_now.version == 2


async def test_write_appends_version_and_audits(org: uuid.UUID) -> None:
    factory = dbmod.get_sessionmaker()
    async with factory() as s:
        v1 = await svc.write_setting(s, org_id=org, key="reply.tone", value="a")
        v2 = await svc.write_setting(s, org_id=org, key="reply.tone", value="b")
        await s.commit()
    assert (v1, v2) == (1, 2)

    conn = await asyncpg.connect(_dsn())
    try:
        actions = [
            r["action"]
            for r in await conn.fetch(
                "SELECT action FROM audit_log WHERE org_id = $1 ORDER BY seq", org
            )
        ]
    finally:
        await conn.close()
    assert actions == ["settings.changed", "settings.changed"]
