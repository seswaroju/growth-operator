"""Transactional pack installer (MVP-040).

Assembles a tenant's whole configuration from a verified bundle in one transaction, per
docs/21-platform/vertical-adapter-layer.md. The six-step pipeline runs inside a single
tenant-scoped transaction; a failure at any step rolls the whole thing back (zero partial
artifact rows) and the `pack_installations` row is marked `failed` with the offending step.
Reinstalling the same bundle digest is a no-op fast path (digest = idempotency key). Uninstall
pauses the org's instances and marks the install `uninstalled`, leaving L3 runtime data and the
catalog schema untouched.

The **policies** step (`approval_policies`, migration 014 / MVP-065) is seeded from each binding's
`tier_defaults` (MVP-044). The **workflows** step (`workflow_definitions`, migration 016 / MVP-072)
still targets a table that does not exist yet, so it remains an explicit **deferred** no-op
(BLOCKERS #14) until MVP-072 lands. The `support` archetype is not seeded (MVP-020), so a binding
for an unseeded archetype is skipped for instances — but its pack-level policy rows are still seeded
(policies are keyed by pack + action, not by archetype).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.writer import AuditEntry, write
from core.packs.bundle import ParsedPack, compute_manifest, load_bundle, serialize_manifest
from core.packs.indexes import generate_index_ddl
from core.tenancy.middleware import org_scoped_session

logger = logging.getLogger("core.packs.installer")

# Steps whose target tables are not built yet — recorded, not executed (BLOCKERS #14).
DEFERRED_STEPS = ("workflows",)

_VERTICALS = Path(__file__).resolve().parents[2] / "verticals"


class InstallError(Exception):
    def __init__(self, step: str, cause: Exception) -> None:
        super().__init__(f"install failed at step {step!r}: {cause}")
        self.step = step
        self.cause = cause


@dataclass
class InstallResult:
    installation_id: UUID
    status: str
    idempotent: bool
    deferred_steps: tuple[str, ...] = DEFERRED_STEPS


@dataclass
class InstallPlan:
    pack: str
    version: str
    catalog_schema_version: int
    prompt_layers: int
    bindings: int
    instances: int
    workflows: int
    integrations: int
    deferred_steps: tuple[str, ...] = DEFERRED_STEPS


class _DryRunRollback(Exception):
    """Carries the plan out of the dry-run transaction, forcing it to roll back."""

    def __init__(self, plan: InstallPlan) -> None:
        self.plan = plan


@dataclass
class _Ctx:
    org_id: UUID
    pack_id: UUID
    parsed: ParsedPack


def resolve_pack_dir(pack_ref: str) -> Path:
    """Map a dev pack ref (a pack slug) to its directory, rejecting path traversal."""
    if not pack_ref.replace("-", "").replace("_", "").isalnum():
        raise InstallError("resolve", ValueError(f"invalid pack ref: {pack_ref!r}"))
    pack_dir = _VERTICALS / pack_ref
    if not (pack_dir / "pack.yaml").is_file():
        raise InstallError("resolve", ValueError(f"unknown pack: {pack_ref!r}"))
    return pack_dir


def _bundle_digest(pack_dir: Path) -> str:
    return hashlib.sha256(serialize_manifest(compute_manifest(pack_dir))).hexdigest()


# ---- Pack row + installation row ------------------------------------------------------


async def _get_or_create_pack(session: AsyncSession, parsed: ParsedPack, digest: str) -> UUID:
    m = parsed.manifest
    return (
        await session.execute(
            text(
                "INSERT INTO packs "
                "(slug, version, platform_api, risk_class, manifest, bundle_uri, signature, "
                " status) "
                "VALUES (:slug, :ver, :api, :risk, CAST(:manifest AS jsonb), :uri, :sig, "
                " 'published') "
                "ON CONFLICT (slug, version) DO UPDATE SET manifest = EXCLUDED.manifest "
                "RETURNING id"
            ),
            {"slug": m.pack, "ver": m.version, "api": m.platform_api,
             "risk": m.risk_class or "standard", "manifest": json.dumps(m.model_dump(mode="json")),
             "uri": f"dev://{m.pack}", "sig": digest},
        )
    ).scalar_one()


async def _find_installed(
    session: AsyncSession, org_id: UUID, pack_id: UUID, digest: str
) -> UUID | None:
    return (
        await session.execute(
            text(
                "SELECT id FROM pack_installations WHERE org_id = :org AND pack_id = :pid "
                "AND status = 'active' AND config->>'_digest' = :digest"
            ),
            {"org": str(org_id), "pid": str(pack_id), "digest": digest},
        )
    ).scalar_one_or_none()


async def _create_installation(
    session: AsyncSession, org_id: UUID, pack_id: UUID, config: dict, digest: str
) -> UUID:
    return (
        await session.execute(
            text(
                "INSERT INTO pack_installations (org_id, pack_id, status, config) "
                "VALUES (:org, :pid, 'installing', CAST(:cfg AS jsonb)) RETURNING id"
            ),
            {"org": str(org_id), "pid": str(pack_id),
             "cfg": json.dumps({**config, "_digest": digest})},
        )
    ).scalar_one()


# ---- The six pipeline steps (uniform signature so each is monkeypatchable) -------------


async def _register_catalog_schema(session: AsyncSession, ctx: _Ctx) -> None:
    c = ctx.parsed.catalog
    identity_cols = list(dict.fromkeys(col for group in c.identity_keys for col in group))
    # Generate the attribute indexes from x-index annotations (MVP-042); a scheduler job
    # applies them CONCURRENTLY later.
    generated_ddl = generate_index_ddl(ctx.parsed.manifest.pack, ctx.pack_id, c.json_schema)
    await session.execute(
        text(
            "INSERT INTO catalog_schemas "
            "(pack_id, version, json_schema, search_projection, identity_keys, generated_ddl) "
            "VALUES (:pid, :ver, CAST(:js AS jsonb), :sp, :ik, :ddl) "
            "ON CONFLICT (pack_id, version) DO NOTHING"
        ),
        {"pid": str(ctx.pack_id), "ver": c.version, "js": json.dumps(c.json_schema),
         "sp": c.search_projection, "ik": identity_cols, "ddl": generated_ddl},
    )


async def _apply_pack_migrations(session: AsyncSession, ctx: _Ctx) -> None:
    return None  # no pack-supplied migrations in the MVP packs


async def _seed_prompt_layers(session: AsyncSession, ctx: _Ctx) -> None:
    for layer in ctx.parsed.prompt_layers:
        await session.execute(
            text(
                "INSERT INTO prompt_layers "
                "(layer_type, pack_id, archetype, task, version, content, requires, status) "
                "SELECT 'vertical', :pid, :arch, :task, :ver, :content, CAST(:req AS jsonb), "
                "       'candidate' "
                "WHERE NOT EXISTS (SELECT 1 FROM prompt_layers WHERE pack_id = :pid "
                "  AND archetype = :arch AND task = :task AND version = :ver "
                "  AND layer_type = 'vertical')"
            ),
            {"pid": str(ctx.pack_id), "arch": layer.archetype, "task": layer.task,
             "ver": layer.version, "content": layer.content, "req": json.dumps(layer.requires)},
        )


# The pack tier rules time out with a domain verb (e.g. `hold_and_remind`); the DB CHECK allows only
# hold|safe_default|cancel (the remind is the ladder's job, MVP-068), so map to the base action.
_ON_TIMEOUT_MAP = {"hold_and_remind": "hold", "hold": "hold",
                   "safe_default": "safe_default", "cancel": "cancel"}
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_duration_s(value: str | None) -> int | None:
    """`30m` -> 1800. Returns None for absent/unrecognised (no timeout)."""
    if not value:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", value)
    return int(m.group(1)) * _DURATION_UNITS[m.group(2)] if m else None


async def _seed_policies(session: AsyncSession, ctx: _Ctx) -> None:
    """Seed `approval_policies` (scope='pack') from every binding's `tier_defaults` (MVP-044).
    `action_type` is the rule's `applies_to` verbatim (faithful to the pack; the tool→action
    bridge that makes these fire on tool calls is a follow-up). Idempotent per (pack, action)."""
    for binding in ctx.parsed.bindings.bindings:
        for rule in binding.tier_defaults:
            await session.execute(
                text(
                    "INSERT INTO approval_policies (scope, pack_id, action_type, tier, cel_expr, "
                    " description, approver_chain, timeout_s, on_timeout, confirm_kind) "
                    "SELECT 'pack', :pid, :at, :tier, :cel, :desc, CAST(:chain AS jsonb), :to, "
                    "       :ot, :ck "
                    "WHERE NOT EXISTS (SELECT 1 FROM approval_policies WHERE pack_id = :pid "
                    "  AND scope = 'pack' AND action_type = :at AND description = :desc)"
                ),
                {"pid": str(ctx.pack_id), "at": rule.applies_to, "tier": rule.tier,
                 "cel": rule.condition, "desc": rule.description or rule.rule_key,
                 "chain": json.dumps([rule.approver] if rule.approver else []),
                 "to": _parse_duration_s(rule.timeout),
                 "ot": _ON_TIMEOUT_MAP.get(rule.on_timeout or "hold", "hold"),
                 "ck": rule.confirm},
            )


async def _seed_workflows(session: AsyncSession, ctx: _Ctx) -> None:
    # DEFERRED: workflow_definitions (migration 016 / MVP-072) not built — BLOCKERS #14, MVP-044.
    return None


async def _create_bindings_and_instances(session: AsyncSession, ctx: _Ctx) -> None:
    for b in ctx.parsed.bindings.bindings:
        archetype_id = (
            await session.execute(
                text("SELECT id FROM agent_archetypes WHERE slug = :s"), {"s": b.archetype}
            )
        ).scalar_one_or_none()
        if archetype_id is None:
            logger.warning("archetype %r not seeded; skipping binding", b.archetype)
            continue
        binding_id = (
            await session.execute(
                text(
                    "INSERT INTO agent_bindings "
                    "(pack_id, archetype_id, persona_default, tool_grants, kpi_defs, "
                    " tier_defaults) "
                    "VALUES (:pid, :aid, :persona, CAST(:tg AS jsonb), CAST(:kd AS jsonb), "
                    "        CAST(:td AS jsonb)) "
                    "ON CONFLICT (pack_id, archetype_id) DO UPDATE SET "
                    "  persona_default = EXCLUDED.persona_default RETURNING id"
                ),
                {"pid": str(ctx.pack_id), "aid": archetype_id, "persona": b.persona_default,
                 "tg": json.dumps([g.model_dump() for g in b.tool_grants]),
                 "kd": json.dumps({"kpis": b.kpis, "budgets": b.budgets}),
                 "td": json.dumps([r.model_dump() for r in b.tier_defaults])},
            )
        ).scalar_one()
        manifest = {
            "archetype": b.archetype,
            "tool_grants": [g.model_dump() for g in b.tool_grants],
        }
        await session.execute(
            text(
                "INSERT INTO agent_instances "
                "(org_id, binding_id, persona_name, status, permission_manifest, budget_caps) "
                "SELECT :org, :bid, :persona, 'paused', CAST(:pm AS jsonb), CAST(:bc AS jsonb) "
                "WHERE NOT EXISTS (SELECT 1 FROM agent_instances "
                "  WHERE org_id = :org AND binding_id = :bid)"
            ),
            {"org": str(ctx.org_id), "bid": str(binding_id), "persona": b.persona_default,
             "pm": json.dumps(manifest), "bc": json.dumps(b.budgets)},
        )


async def _activate_prompts(session: AsyncSession, ctx: _Ctx) -> None:
    """Pin a base+vertical+tenant prompt binding per (instance, task) so the composer can render a
    grounded prompt (executor→composer pipeline). Seeds the platform base layer, generates the org's
    tenant layer from settings, and pins them with the pack's vertical layer. An archetype with no
    base layer (or a compat mismatch) is skipped — those runs fall back to the skeleton prompt."""
    from core.prompts.base_layers import ensure_base_layer
    from core.prompts.registry import IncompatiblePin, pin_binding
    from core.prompts.tenant_layer import generate_tenant_layer

    for b in ctx.parsed.bindings.bindings:
        instance_id = (
            await session.execute(
                text(
                    "SELECT i.id FROM agent_instances i "
                    "JOIN agent_bindings ab ON ab.id = i.binding_id "
                    "JOIN agent_archetypes a ON a.id = ab.archetype_id "
                    "WHERE i.org_id = :o AND ab.pack_id = :p AND a.slug = :arch"
                ),
                {"o": str(ctx.org_id), "p": str(ctx.pack_id), "arch": b.archetype},
            )
        ).scalar_one_or_none()
        if instance_id is None:
            continue  # archetype not seeded (e.g. support)
        base_id = await ensure_base_layer(session, b.archetype)
        if base_id is None:
            continue  # no base layer for this archetype → skeleton fallback
        for task in b.tasks:
            anchor = task.prompt_layer.ref.rsplit("#", 1)[-1]  # e.g. prompts/…#catalog → 'catalog'
            vertical_id = (
                await session.execute(
                    text(
                        "SELECT id FROM prompt_layers WHERE pack_id = :p "
                        "AND layer_type = 'vertical' AND archetype = :arch AND task = :t"
                    ),
                    {"p": str(ctx.pack_id), "arch": b.archetype, "t": anchor},
                )
            ).scalar_one_or_none()
            if vertical_id is None:
                continue
            tenant_id = await generate_tenant_layer(session, ctx.org_id, b.archetype, task.task)
            try:
                await pin_binding(
                    session, org_id=ctx.org_id, agent_instance_id=instance_id, task=task.task,
                    base_layer=base_id, vertical_layer=vertical_id, tenant_layer=tenant_id,
                )
            except IncompatiblePin:
                logger.warning("prompt compat mismatch %s/%s; skipping pin", b.archetype, task.task)


def _steps() -> list[tuple[str, Callable[[AsyncSession, _Ctx], Awaitable[None]]]]:
    # Rebuilt per install so monkeypatched step functions are picked up (rollback tests).
    return [
        ("catalog_schema", _register_catalog_schema),
        ("pack_migrations", _apply_pack_migrations),
        ("prompt_layers", _seed_prompt_layers),
        ("policies", _seed_policies),
        ("workflows", _seed_workflows),
        ("bindings_instances", _create_bindings_and_instances),
        ("prompts_activate", _activate_prompts),
    ]


# ---- Public API -----------------------------------------------------------------------


async def _set_status(session: AsyncSession, installation_id: UUID, status: str) -> None:
    await session.execute(
        text("UPDATE pack_installations SET status = :st WHERE id = :id"),
        {"st": status, "id": str(installation_id)},
    )


async def _mark_failed(
    session: AsyncSession, installation_id: UUID, step: str, error: str
) -> None:
    await session.execute(
        text(
            "UPDATE pack_installations SET status = 'failed', "
            "config = config || CAST(:err AS jsonb) WHERE id = :id"
        ),
        {"err": json.dumps({"_error_step": step, "_error": error[:500]}),
         "id": str(installation_id)},
    )


async def _audit(
    session: AsyncSession, org_id: UUID, action: str, installation_id: UUID,
    parsed: ParsedPack, digest: str, actor_id: UUID | None,
) -> None:
    await write(
        session,
        AuditEntry(
            org_id=org_id, actor_type="user" if actor_id else "system",
            actor_id=str(actor_id) if actor_id else "installer",
            action=action, resource=str(installation_id),
            payload={"pack": parsed.manifest.pack, "version": parsed.manifest.version,
                     "digest": digest},
        ),
    )


async def install(
    org_id: UUID, pack_dir: Path, config: dict | None = None, *, actor_id: UUID | None = None
) -> InstallResult:
    """Verify + install a pack for an org. Idempotent by bundle digest; rolls back fully on
    any step failure and records the failing step."""
    parsed = load_bundle(pack_dir)
    digest = _bundle_digest(pack_dir)

    async with org_scoped_session(org_id) as s:
        pack_id = await _get_or_create_pack(s, parsed, digest)
        existing = await _find_installed(s, org_id, pack_id, digest)
    if existing is not None:  # reinstall of the same digest → no-op fast path
        return InstallResult(existing, "active", idempotent=True)

    async with org_scoped_session(org_id) as s:
        installation_id = await _create_installation(s, org_id, pack_id, config or {}, digest)

    ctx = _Ctx(org_id=org_id, pack_id=pack_id, parsed=parsed)
    current = ""
    try:
        async with org_scoped_session(org_id) as s:  # single txn through step 6
            for name, fn in _steps():
                current = name
                await fn(s, ctx)
            await _set_status(s, installation_id, "active")
            await _audit(s, org_id, "pack.installed", installation_id, parsed, digest, actor_id)
    except Exception as exc:  # noqa: BLE001 - any step failure rolls back + records failure
        async with org_scoped_session(org_id) as s:
            await _mark_failed(s, installation_id, current, str(exc))
        raise InstallError(current, exc) from exc

    return InstallResult(installation_id, "active", idempotent=False)


async def uninstall(org_id: UUID, installation_id: UUID, *, actor_id: UUID | None = None) -> None:
    """Pause the org's instances for this pack and mark the install uninstalled. The catalog
    schema is retained (render history) and all L3 runtime data is left untouched. Attribute
    freeze (catalog_items, migration 012) and credential revocation are deferred (BLOCKERS #14)."""
    async with org_scoped_session(org_id) as s:
        pack_id = (
            await s.execute(
                text("SELECT pack_id FROM pack_installations WHERE id = :id AND org_id = :org"),
                {"id": str(installation_id), "org": str(org_id)},
            )
        ).scalar_one_or_none()
        if pack_id is None:
            raise InstallError("uninstall", ValueError("installation not found"))
        await s.execute(
            text(
                "UPDATE agent_instances SET status = 'paused' WHERE org_id = :org "
                "AND binding_id IN (SELECT id FROM agent_bindings WHERE pack_id = :pid)"
            ),
            {"org": str(org_id), "pid": str(pack_id)},
        )
        await _set_status(s, installation_id, "uninstalled")
        await write(
            s,
            AuditEntry(
                org_id=org_id, actor_type="user" if actor_id else "system",
                actor_id=str(actor_id) if actor_id else "installer",
                action="pack.uninstalled", resource=str(installation_id), payload={},
            ),
        )


async def _plan_counts(
    session: AsyncSession, org_id: UUID, pack_id: UUID, parsed: ParsedPack
) -> InstallPlan:
    async def _count(sql: str, param: dict) -> int:
        return (await session.execute(text(sql), param)).scalar_one()

    return InstallPlan(
        pack=parsed.manifest.pack, version=parsed.manifest.version,
        catalog_schema_version=parsed.catalog.version,
        prompt_layers=await _count(
            "SELECT count(*) FROM prompt_layers WHERE pack_id = :p", {"p": str(pack_id)}
        ),
        bindings=await _count(
            "SELECT count(*) FROM agent_bindings WHERE pack_id = :p", {"p": str(pack_id)}
        ),
        instances=await _count(
            "SELECT count(*) FROM agent_instances WHERE org_id = :o", {"o": str(org_id)}
        ),
        workflows=len(parsed.workflows), integrations=len(parsed.integrations),
    )


async def dry_run(org_id: UUID, pack_dir: Path) -> InstallPlan:
    """Validate + plan an install without persisting anything (MVP-043). Runs the real pipeline
    inside a transaction that is always rolled back, so it catches any core hardcoding that
    would break a second pack while writing nothing. A contract violation raises `BundleError`."""
    parsed = load_bundle(pack_dir)
    digest = _bundle_digest(pack_dir)
    try:
        async with org_scoped_session(org_id) as s:
            pack_id = await _get_or_create_pack(s, parsed, digest)
            ctx = _Ctx(org_id=org_id, pack_id=pack_id, parsed=parsed)
            for _name, fn in _steps():
                await fn(s, ctx)
            raise _DryRunRollback(await _plan_counts(s, org_id, pack_id, parsed))
    except _DryRunRollback as rollback:  # the txn rolled back → nothing persisted
        return rollback.plan


async def list_packs(session: AsyncSession) -> list[dict]:
    """Published packs available to install (global registry — not org-scoped)."""
    rows = (
        await session.execute(
            text(
                "SELECT id, slug, version, risk_class, status, "
                "manifest->>'display_name' AS display_name FROM packs "
                "WHERE status = 'published' ORDER BY slug, version"
            )
        )
    ).mappings().all()
    return [dict(r) for r in rows]
