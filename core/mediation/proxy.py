"""Mediation proxy — the ONLY path from model tool-calls to tool implementations (MVP-060).

`call()` runs the authoritative check chain, in order (docs/21-platform/tool-permission-model.md):

    manifest integrity → grant lookup → untrusted narrowing → param constraints →
    rate limit → budgets → tier (approval) → audit intent (log-then-act) → execute → egress scrub

A denial returns a **structured, recoverable `ToolError`** the model can adapt to — never the
manifest contents. Repeated manifest violations (≥3 in a run) abort the run (`RunAborted`) and the
denial is audited + alerted. The runtime reaches tools only through here (enforced by the
`runtime-not-tools` lint guard), so manifests/params/rates/budgets/tiers/audit cannot be bypassed.

Deferred (disclosed): ed25519 manifest **signature** verification (hash integrity is checked now);
the live **policy engine** for tier decisions (stubbed — MVP-065); a real **PII egress** filter
(pass-through hook for now). Scalar policy constraints (e.g. `conversation_scope`) are recorded but
enforced by the policy engine later; schema-shaped constraints are enforced now.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import jsonschema
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.writer import AuditEntry
from core.audit.writer import write as audit_write
from core.mediation import limits
from core.mediation import manifest as manifest_module

MAX_MANIFEST_VIOLATIONS = 3  # AC: ≥3 manifest violations aborts the run
DEFAULT_TOOL_TIMEOUT_S = 30


@dataclass
class RunContext:
    org_id: UUID
    run_id: UUID
    instance_id: UUID
    manifest: dict[str, Any]
    manifest_hash: str
    untrusted: bool = False
    actor_id: UUID | None = None
    # Tools already approved for this (resumed) run — the tier gate is skipped for them (MVP-069).
    approved: frozenset[str] = frozenset()


@dataclass
class ToolError:
    """Model-facing structured error (a recoverable signal, NOT an RFC 7807 platform error)."""

    code: str
    message: str
    recoverable: bool = True


@dataclass
class ApprovalPending:
    tier: int
    reason: str


@dataclass
class ToolResult:
    ok: bool
    output: Any = None
    error: ToolError | None = None
    pending: ApprovalPending | None = None
    audit_id: UUID | None = None


class RunAborted(Exception):
    """Raised when a run exceeds the manifest-violation threshold; the executor interrupts it."""

    def __init__(self, run_id: UUID, violations: int) -> None:
        super().__init__(f"run {run_id} aborted after {violations} manifest violations")
        self.run_id = run_id
        self.violations = violations


# A tool implementation: (ctx, params, session, audit_id) -> output. Registered in mediation.tools.
ToolImpl = Callable[[RunContext, dict[str, Any], AsyncSession, UUID], Awaitable[Any]]

# Tier evaluator (stubbed until the policy engine, MVP-065). Conservative default below.
TierEvaluator = Callable[[RunContext, str, dict[str, Any]], int]


def _find_grant(manifest: dict[str, Any], tool_name: str) -> dict[str, Any] | None:
    for grant in manifest.get("tools", []):
        name = grant.get("name", "")
        if name == tool_name or (name.endswith(".*") and tool_name.startswith(name[:-1])):
            return grant
    return None


def _validate_params(params: dict[str, Any], constraints: dict[str, Any] | None) -> str | None:
    """Enforce schema-shaped constraints (e.g. {"strategy": {"enum": [...]}}). Scalar policy
    constraints are left to the policy engine. Returns an error message, or None if valid.

    PILOT-1C: a **list-shaped** constraint now refuses the call instead of being skipped. Installed
    packs carried grants of the form `{"<param>": ["<allowed>"]}`, which read like an allow-list and
    enforced nothing — non-dict entries were filtered out here, so an agent's only send constraint
    was silently inert. A constraint the platform cannot enforce is worse than no constraint,
    because someone wrote it and believed it. Scalars stay permitted: they are documented
    policy-engine inputs, not failed enums.
    """
    if not constraints:
        return None
    listish = sorted(k for k, v in constraints.items() if isinstance(v, (list, tuple)))
    if listish:
        return (f"params_constraints {listish} are written as bare lists, which enforce nothing; "
                'write them as {"<param>": {"enum": [...]}}')
    schema_props = {k: v for k, v in constraints.items() if isinstance(v, dict)}
    if not schema_props:
        return None
    try:
        jsonschema.validate(
            params, {"type": "object", "properties": schema_props},
            cls=jsonschema.Draft202012Validator,
        )
    except jsonschema.ValidationError as exc:
        return exc.message
    return None


async def _engine_tier(
    session: AsyncSession, ctx: RunContext, tool: str, params: dict[str, Any]
) -> int:
    """Live tier from the policy engine (MVP-065). The tool is resolved to its abstract-action
    family so the pack's tier rules (keyed by `action.*`) fire — max tier wins (BLOCKERS #20)."""
    from core.approvals.engine import evaluate_tool

    decision = await evaluate_tool(
        session, org_id=ctx.org_id, actor_instance_id=ctx.instance_id,
        untrusted=ctx.untrusted, tool=tool, params=params,
    )
    return decision.tier


async def _publish_alert(redis: Redis, kind: str, detail: dict[str, Any]) -> None:
    envelope = {
        "specversion": "1.0", "id": str(uuid4()), "type": "alert.ops.v1",
        "source": "gop/mediation", "time": datetime.now(UTC).isoformat(),
        "data": {"severity": "error", "kind": kind, "detail": detail},
    }
    await redis.xadd("gop:events:alert.ops.v1", {"data": json.dumps(envelope)})


async def _audit(
    session: AsyncSession, ctx: RunContext, action: str, payload: dict[str, Any]
) -> UUID:
    entry = await audit_write(
        session,
        AuditEntry(
            org_id=ctx.org_id, actor_type="agent", actor_id=str(ctx.instance_id),
            action=action, resource=str(ctx.run_id), payload=payload,
            permission_manifest_hash=ctx.manifest_hash,
        ),
    )
    return entry.id


async def _manifest_denied(
    session: AsyncSession, redis: Redis, ctx: RunContext, tool: str, reason: str
) -> ToolResult:
    """Audit + alert a manifest-scope denial, bump the run's violation counter, and abort at ≥3."""
    await _audit(session, ctx, f"tool.{tool}:denied", {"reason": reason})
    await _publish_alert(redis, "manifest_violation",
                         {"run_id": str(ctx.run_id), "tool": tool, "reason": reason})
    count = int(await redis.incr(f"gop:run:{ctx.run_id}:violations"))
    if count >= MAX_MANIFEST_VIOLATIONS:
        raise RunAborted(ctx.run_id, count)
    return ToolResult(
        ok=False,
        error=ToolError("permission_denied_manifest", f"{tool}: {reason}", recoverable=True),
    )


async def _commercial_denial(session: Any, ctx: RunContext, tool_name: str) -> ToolResult | None:
    """`None` when the run may proceed, else a structured refusal.

    Returns a refusal rather than raising: a plan denial is an expected outcome that must close the
    run cleanly, exactly as a send-gate refusal does — never a crash that trips the breaker."""
    from core.mediation.tools import TOOL_CAPABILITY, TOOL_PLAN_EXEMPT
    from core.tenancy.entitlements import AgentNotExecutable, assert_agent_executable, is_entitled

    try:
        await assert_agent_executable(session, ctx.org_id, ctx.instance_id)
    except AgentNotExecutable as exc:
        return ToolResult(ok=False, error=ToolError("permission_denied_manifest", str(exc)))

    capability = TOOL_CAPABILITY.get(tool_name)
    if capability is None:
        if tool_name in TOOL_PLAN_EXEMPT:
            return None
        # Unclassified tool: fail closed. The CI guard prevents this reaching production.
        return ToolResult(
            ok=False,
            error=ToolError("permission_denied_manifest",
                            f"{tool_name} has no commercial classification"))
    if not await is_entitled(session, ctx.org_id, capability):
        return ToolResult(
            ok=False,
            error=ToolError("permission_denied_manifest",
                            f"{capability} is not included in this plan"))
    return None


async def call(
    ctx: RunContext, tool_name: str, params: dict[str, Any], *,
    session: AsyncSession, redis: Redis,
    registry: dict[str, ToolImpl] | None = None,
    tier_eval: TierEvaluator | None = None,
) -> ToolResult:
    """Mediate one tool call through the ordered check chain. Never raises for a *denial* (returns
    a recoverable ToolError); raises RunAborted only when the violation threshold is crossed."""
    from core.mediation.tools import REGISTRY

    registry = registry if registry is not None else REGISTRY  # tier_eval None → live engine

    # 1. manifest integrity — the run's pinned hash matches, the manifest's own hash matches its
    #    body, and the ed25519 signature is valid (MVP-061). Any failure denies + counts a
    #    violation (a forged/stale/tampered manifest fails closed and can abort the run).
    if ctx.manifest_hash != manifest_module.manifest_hash(ctx.manifest) or \
            not manifest_module.verify(ctx.manifest):
        return await _manifest_denied(session, redis, ctx, tool_name, "manifest integrity failed")

    # 1b. freshness — the pinned manifest must match the instance's CURRENT compiled manifest; a
    #     grant change recompiles the instance, so a run on the old hash is denied until it does
    #     too (MVP-061). Skipped when the instance is not persisted (hermetic proxy tests).
    current = (
        await session.execute(
            text("SELECT permission_manifest ->> 'hash' FROM agent_instances WHERE id = :i"),
            {"i": str(ctx.instance_id)},
        )
    ).scalar_one_or_none()
    if current is not None and current != f"sha256:{ctx.manifest_hash}":
        return await _manifest_denied(session, redis, ctx, tool_name, "stale manifest (recompile)")

    # 2. grant present?
    grant = _find_grant(ctx.manifest, tool_name)
    if grant is None:
        return await _manifest_denied(session, redis, ctx, tool_name, "tool not in manifest")

    # 3. untrusted-content narrowing — a run that has ingested external content (this call or an
    #    earlier one in the run) may use only the manifest's narrowing-allow tools (MVP-062).
    allow = ctx.manifest.get("untrusted_narrowing", {}).get("allow", [])
    if (ctx.untrusted or await limits.is_untrusted(redis, ctx.run_id)) and tool_name not in allow:
        return await _manifest_denied(session, redis, ctx, tool_name, "narrowed under untrusted")

    # 4. param constraints
    perr = _validate_params(params, grant.get("params_constraints"))
    if perr is not None:
        return ToolResult(ok=False, error=ToolError("config_schema_violation", perr))

    # 5. rate limit — 60s sliding window per (instance, tool) (MVP-062).
    per_min = (grant.get("rate_limit") or {}).get("per_min")
    if not await limits.check_rate(redis, ctx.instance_id, tool_name, per_min):
        return ToolResult(ok=False, error=ToolError("rate_limited", f"{tool_name} rate exceeded"))

    # 6. budgets — the daily send cap (a hard external cost); counter recorded at the send boundary.
    breach = limits.budget_breach(ctx.manifest.get("budgets", {}))
    if breach is not None and tool_name == "messages.send":
        kind, cap = breach
        if not await limits.check_budget(redis, ctx.instance_id, kind, cap):
            limits.log_budget_breach(ctx.instance_id, kind, cap)
            return ToolResult(
                ok=False, error=ToolError("budget_exceeded", f"{kind} {cap}/day exhausted",
                                          recoverable=False))

    # 7. tier — the live policy engine (MVP-065) decides; an injected evaluator overrides it for
    # hermetic tests. Tier ≥ 2 checkpoints the run for approval (no side effect yet). A tool that
    # was already approved for this resumed run (MVP-069) skips the gate and executes.
    if grant.get("requires_tier_eval") and tool_name not in ctx.approved:
        if tier_eval is not None:
            tier = tier_eval(ctx, tool_name, params)
        else:
            tier = await _engine_tier(session, ctx, tool_name, params)
        if tier >= 2:
            return ToolResult(
                ok=False, pending=ApprovalPending(tier, f"{tool_name} needs approval")
            )

    # 7b. commercial authority (PLAN-5) — the last gate before an effect, and the reason the HTTP
    # gates alone are not sufficient: an agent reaches these tools without touching a route. Both
    # the *agent* and the *tool's capability* are re-checked against the plan as it stands **now**,
    # so a run started while entitled cannot keep acting after a downgrade.
    denial = await _commercial_denial(session, ctx, tool_name)
    if denial is not None:
        reason = denial.error.message if denial.error else "denied"
        await _audit(session, ctx, f"tool.{tool_name}:denied", {"reason": reason})
        return denial

    # 8. audit intent (log-then-act)
    audit_id = await _audit(session, ctx, f"tool.{tool_name}:intent",
                            {"params": _redact(params)})

    # 9. execute
    impl = registry.get(tool_name)
    if impl is None:
        return ToolResult(
            ok=False, error=ToolError("provider_unavailable", f"{tool_name} not wired"),
            audit_id=audit_id,
        )
    try:
        output = await impl(ctx, params, session, audit_id)
    except Exception as exc:  # a provider failure → structured, recoverable hard failure (MVP-063)
        return ToolResult(
            ok=False, audit_id=audit_id,
            error=ToolError("provider_unavailable", f"{tool_name} failed: {type(exc).__name__}"),
        )

    # A tool that returned external content narrows the run until the next human boundary (MVP-062).
    if limits.result_is_untrusted(tool_name, output):
        await limits.mark_untrusted(redis, ctx.run_id)

    # 10. egress scrub (PII filter hook — pass-through for now)
    return ToolResult(ok=True, output=_egress_scrub(output), audit_id=audit_id)


def _redact(params: dict[str, Any]) -> dict[str, Any]:
    """Keep tool params out of the audit payload verbatim — record only the keys used."""
    return {"keys": sorted(params)}


def _egress_scrub(output: Any) -> Any:
    """PII egress filter hook. A real destination-aware scrubber lands later; pass-through now."""
    return output
