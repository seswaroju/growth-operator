"""Tenant settings service (MVP-021) — see docs/21-platform/tenant-configuration.md.

`resolve()` returns a value with **provenance** using deterministic 4-layer precedence:

    flag (config-type override) > tenant (latest version row) > pack (installed pack
    manifest default) > platform (core defaults registry)

Writes never UPDATE — they append a new version row (so `resolve_at()` can reconstruct the
config at any past instant, for incident forensics), bump the org's settings version, and
publish an invalidation on the Redis `settings:{org}` channel. Every write also appends a
`settings.changed` audit entry with the diff (MVP-024).

**Tighten-only autonomy keys**: a write that *loosens* autonomy is rejected unless the org's
trust threshold is met. The trust ledger lands in MVP-065, so for now loosening always
fails closed (`TightenOnlyViolation`); tightening/equal is allowed.

Deferred (disclosed): full JSON-Schema validation of `value` vs `schema_ref` needs the
`jsonschema` dependency (BLOCKERS #4) — for now writes validate the key is known and the
tighten-only rule. The in-process 30s cache is a perf layer over this correct path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import AuditEntry
from core.audit import write as audit_write
from core.audit.taxonomy import SETTINGS_CHANGED
from core.common.config import get_settings
from core.tenancy import repository


class SettingSource(StrEnum):
    FLAG = "flag"
    TENANT = "tenant"
    PACK = "pack"
    PLATFORM = "platform"


@dataclass
class Resolved:
    value: Any
    source: SettingSource
    schema_ref: str | None = None
    version: int | None = None  # the tenant version when source == TENANT


@dataclass(frozen=True)
class PlatformDefault:
    value: Any
    schema_ref: str | None = None
    tighten_only: bool = False


# Autonomy ladder — higher index == looser (more autonomous). "Loosening" moves up.
AUTONOMY_LADDER: tuple[str, ...] = ("off", "draft_only", "suggest", "auto")

PLATFORM_DEFAULTS: dict[str, PlatformDefault] = {
    "reply.tone": PlatformDefault("warm", schema_ref="core.reply_tone"),
    "quiet_hours.start": PlatformDefault("21:00", schema_ref="core.time"),
    # Autonomy "volume knob" (Ticket 3.6). The owner **free-dials** how much the assistant handles
    # on its own, per capability — `tighten_only=False` supersedes the old tighten-only rule
    # (DECISIONS 2026-08-06): loosening no longer needs an earned trust threshold. Default `auto`
    # = respect the pack/tier rules (routine auto-sends, risky parks), so wiring the knob does not
    # change existing behaviour until an owner tightens it. The engine's autonomy overlay can only
    # RAISE a tier, so the CORE_TIER4_ACTIONS money floor stays absolute at every knob position.
    "autonomy.messaging": PlatformDefault("auto", schema_ref="core.autonomy"),
    "autonomy.pricing": PlatformDefault("auto", schema_ref="core.autonomy"),
    "autonomy.campaigns": PlatformDefault("auto", schema_ref="core.autonomy"),
    # Global "pause all autonomy" panic switch — on ⇒ every capability forces approval.
    "autonomy.paused": PlatformDefault(False, schema_ref="core.bool"),
}


class UnknownConfigKey(Exception):
    pass


class TightenOnlyViolation(Exception):
    """Raised when a write would loosen a tighten-only (autonomy) key."""


# ---- Resolution ------------------------------------------------------------


async def _flag_override(session: AsyncSession, org_id: UUID, key: str) -> Resolved | None:
    row = (
        await session.execute(
            text(
                "SELECT r.value FROM feature_flags f JOIN flag_rules r ON r.flag_id = f.id "
                "WHERE f.key = :key AND f.flag_type = 'config' "
                "AND r.scope = 'tenant' AND r.scope_ref = :org "
                "ORDER BY r.precedence LIMIT 1"
            ),
            {"key": key, "org": str(org_id)},
        )
    ).mappings().first()
    if row is None:
        return None
    return Resolved(value=row["value"], source=SettingSource.FLAG)


async def _tenant_setting(session: AsyncSession, org_id: UUID, key: str) -> Resolved | None:
    row = (
        await session.execute(
            text(
                "SELECT value, schema_ref, version FROM tenant_settings "
                "WHERE org_id = :org AND key = :key ORDER BY version DESC LIMIT 1"
            ),
            {"org": str(org_id), "key": key},
        )
    ).mappings().first()
    if row is None:
        return None
    return Resolved(
        value=row["value"], source=SettingSource.TENANT,
        schema_ref=row["schema_ref"], version=row["version"],
    )


async def _pack_default(session: AsyncSession, org_id: UUID, key: str) -> Resolved | None:
    row = (
        await session.execute(
            text(
                "SELECT p.manifest FROM pack_installations pi JOIN packs p ON p.id = pi.pack_id "
                "WHERE pi.org_id = :org AND pi.status = 'active' ORDER BY pi.priority LIMIT 1"
            ),
            {"org": str(org_id)},
        )
    ).mappings().first()
    if row is None:
        return None
    defaults = row["manifest"].get("config_defaults", {})
    if key not in defaults:
        return None
    return Resolved(value=defaults[key], source=SettingSource.PACK)


async def resolve(session: AsyncSession, org_id: UUID, key: str) -> Resolved:
    """Resolve `key` for `org_id` with provenance (flag > tenant > pack > platform)."""
    # Tenant context so the RLS-scoped reads (tenant_settings, pack_installations) see rows.
    await repository.set_org_context(session, org_id)
    for layer in (_flag_override, _tenant_setting, _pack_default):
        resolved = await layer(session, org_id, key)
        if resolved is not None:
            return resolved
    default = PLATFORM_DEFAULTS.get(key)
    if default is None:
        raise UnknownConfigKey(key)
    return Resolved(
        value=default.value, source=SettingSource.PLATFORM, schema_ref=default.schema_ref
    )


async def resolve_at(
    session: AsyncSession, org_id: UUID, key: str, ts: datetime
) -> Resolved:
    """Point-in-time resolve: the tenant value in effect at `ts` (walks version history),
    falling back to pack/platform. Used by incident forensics."""
    await repository.set_org_context(session, org_id)
    row = (
        await session.execute(
            text(
                "SELECT value, schema_ref, version FROM tenant_settings "
                "WHERE org_id = :org AND key = :key AND updated_at <= :ts "
                "ORDER BY version DESC LIMIT 1"
            ),
            {"org": str(org_id), "key": key, "ts": ts},
        )
    ).mappings().first()
    if row is not None:
        return Resolved(
            value=row["value"], source=SettingSource.TENANT,
            schema_ref=row["schema_ref"], version=row["version"],
        )
    pack = await _pack_default(session, org_id, key)
    if pack is not None:
        return pack
    default = PLATFORM_DEFAULTS.get(key)
    if default is None:
        raise UnknownConfigKey(key)
    return Resolved(
        value=default.value, source=SettingSource.PLATFORM, schema_ref=default.schema_ref
    )


# ---- Writes ----------------------------------------------------------------


def _is_looser(key: str, new_value: Any, baseline: Any) -> bool:
    """True iff `new_value` is a looser autonomy level than `baseline` on the ladder."""
    try:
        return AUTONOMY_LADDER.index(new_value) > AUTONOMY_LADDER.index(baseline)
    except ValueError:
        return False  # unknown level → not treated as looser (schema validation catches it)


async def write_setting(
    session: AsyncSession,
    *,
    org_id: UUID,
    key: str,
    value: Any,
    updated_by: UUID | None = None,
) -> int:
    """Append a new version of `key` for `org_id`. Returns the new version number.

    Enforces tighten-only autonomy (fail-closed until MVP-065 trust ledger), writes a
    `settings.changed` audit entry, and publishes cache invalidation on `settings:{org}`.
    """
    default = PLATFORM_DEFAULTS.get(key)
    if default is None:
        raise UnknownConfigKey(key)

    if default.tighten_only and _is_looser(key, value, default.value):
        # Loosening requires a trust threshold that does not exist yet → reject.
        raise TightenOnlyViolation(
            f"{key}: loosening autonomy to {value!r} requires trust threshold (unavailable)"
        )

    await repository.set_org_context(session, org_id)
    prev = await _tenant_setting(session, org_id, key)
    old_value = prev.value if prev else default.value
    new_version = (prev.version or 0) + 1 if prev else 1

    await session.execute(
        text(
            "INSERT INTO tenant_settings (org_id, key, value, schema_ref, version, updated_by) "
            "VALUES (:org, :key, CAST(:value AS jsonb), :schema_ref, :version, :updated_by)"
        ),
        {
            "org": str(org_id), "key": key, "value": json.dumps(value),
            "schema_ref": default.schema_ref, "version": new_version, "updated_by": updated_by,
        },
    )

    # Audit the change with a diff (same transaction as the write).
    await audit_write(
        session,
        AuditEntry(
            org_id=org_id, actor_type="user",
            actor_id=str(updated_by) if updated_by else None,
            action=SETTINGS_CHANGED, resource=key,
            payload={"key": key, "old": old_value, "new": value, "version": new_version},
        ),
    )
    return new_version


async def publish_invalidation(org_id: UUID, key: str) -> None:
    """Publish a settings-changed message so other processes drop cached values. Best-effort;
    the version-keyed cache also self-heals. Called after the write transaction commits."""
    redis: Redis = Redis.from_url(get_settings().redis_url)
    try:
        await redis.publish(f"settings:{org_id}", key)
    finally:
        await redis.aclose()
