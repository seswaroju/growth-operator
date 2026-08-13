"""`tool_call` — the workflow engine's one way to cause an external effect (PILOT-1C).

Before this, a workflow could reason and it could ask a human, but no step could actually *do*
anything: the recovery playbook diagnosed a ghost, collected the owner's decision, and then waited
for a reply to a message nobody had sent. This closes that gap with the smallest primitive that can:
resolve inputs, invoke an **existing** mediated tool, bind the result.

What it deliberately is not: no retry policy, no parallelism, no tool-specific branching, no
provider import. Naming a tool here grants nothing — the manifest, the acting principal's execution
authority, the tool's own capability gate, rate limits, budgets and the tier engine all still apply
inside `core.mediation.proxy.call`, which is the only door this module knows.

**The acting principal is never named by the DSL.** A step that could choose whose manifest it runs
under would be an escalation primitive. Instead the principal is derived from the run's *persisted*
workflow identity (`workflow_definitions.workflow_key`, `origin='pack'`) through the internal-worker
registry, so a tenant-authored workflow copying this step verbatim resolves no principal and the
step fails closed.

**Input resolution is strict.** A plain string is a required reference into the run's variables: if
it does not resolve, the step fails rather than passing a literal `"subject.conversation_id"` to a
tool. `{const: ...}` is a literal and `{ref: ..., required: false}` is optional — both explicit,
because guessing which strings are paths is how a lead id becomes a template parameter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import text

from core.mediation import manifest as manifest_module
from core.runtime.internal_workers import GRANTS, InternalWorkerGrant
from core.tenancy.middleware import org_scoped_session
from core.tenancy.repository import set_org_context

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

#: Raised through the step result, never as an exception — a workflow step failing is normal
#: control flow (it runs compensation), not a platform error.
UNRESOLVED = object()


class ToolStepError(Exception):
    """The step cannot run as written. Deterministic: same run + same vars → same failure."""

    def __init__(self, reason: str, **detail: object):
        self.reason = reason
        self.detail = detail
        super().__init__(reason)


# ---- input resolution -----------------------------------------------------------------


def _walk(ref: str, activation: dict[str, Any]) -> Any:
    node: Any = activation
    for part in ref.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return UNRESOLVED
    return node


def resolve_inputs(input_map: dict[str, Any], activation: dict[str, Any]) -> dict[str, Any]:
    """Resolve a step's `input_map` against the run's variables.

    * ``"subject.lead_id"`` — a **required** reference; unresolved raises `ToolStepError`.
    * ``{const: "x"}``      — a literal, never treated as a path.
    * ``{ref: "vars.x", required: false}`` — optional; omitted entirely when unresolved, so the
      tool sees an absent key rather than a null it might interpret as a value.
    """
    resolved: dict[str, Any] = {}
    missing: list[str] = []
    for name, spec in input_map.items():
        if isinstance(spec, dict):
            if "const" in spec:
                resolved[name] = spec["const"]
                continue
            ref = spec.get("ref")
            required = bool(spec.get("required", True))
        elif isinstance(spec, str):
            ref, required = spec, True
        else:  # a number/bool written directly is a literal
            resolved[name] = spec
            continue

        value = _walk(ref, activation) if isinstance(ref, str) else UNRESOLVED
        if value is UNRESOLVED or value is None:
            if required:
                missing.append(name)
            continue
        resolved[name] = value

    if missing:
        raise ToolStepError("input_unresolved", missing=sorted(missing))
    return resolved


# ---- the acting principal -------------------------------------------------------------


@dataclass(frozen=True)
class ToolPrincipal:
    grant: InternalWorkerGrant
    instance_id: UUID
    manifest: dict[str, Any]


async def resolve_principal(org_id: UUID, run_id: UUID) -> ToolPrincipal:
    """Derive whose authority this step runs under, from **persisted** facts only.

    The chain is: workflow run → its definition's `workflow_key` (and that the definition is a
    certified `pack` workflow) → the internal-worker grant registered for that key → the org's
    active instance of that archetype → that instance's signed permission manifest.

    Every link is a database fact or a code constant. Nothing here can be influenced by an event
    payload, an HTTP body, model output, or a field in the workflow DSL.
    """
    async with org_scoped_session(org_id) as s:
        await set_org_context(s, org_id)
        row = (await s.execute(
            text("SELECT d.workflow_key, d.origin, d.status FROM workflow_runs r "
                 "JOIN workflow_definitions d ON d.id = r.definition_id "
                 "WHERE r.id = :r AND r.org_id = :o"),
            {"r": str(run_id), "o": str(org_id)})).mappings().first()
        if row is None:
            raise ToolStepError("workflow_unknown")
        if row["origin"] != "pack":
            # An owner-built workflow may not borrow an internal worker's authority, however it is
            # written. Sellable agents are reached by buying them, not by copying a step.
            raise ToolStepError("untrusted_workflow_origin", origin=row["origin"])

        grant = next((g for g in GRANTS if g.workflow_key == row["workflow_key"]), None)
        if grant is None:
            raise ToolStepError("no_worker_grant", workflow_key=row["workflow_key"])

        instance = (await s.execute(
            text("SELECT i.id, i.permission_manifest FROM agent_instances i "
                 "JOIN agent_bindings b ON b.id = i.binding_id "
                 "JOIN agent_archetypes a ON a.id = b.archetype_id "
                 "WHERE i.org_id = :o AND a.slug = :arch AND i.status = 'active' LIMIT 1"),
            {"o": str(org_id), "arch": grant.archetype})).mappings().first()

    if instance is None:
        raise ToolStepError("no_active_instance", archetype=grant.archetype)
    manifest = dict(instance["permission_manifest"] or {})
    if not manifest:
        raise ToolStepError("no_manifest", archetype=grant.archetype)

    from core.tenancy.entitlements import (
        AgentNotExecutable,
        FeatureNotInPlan,
        assert_agent_executable,
        assert_entitled,
    )

    # Authority is re-verified HERE, at the moment of effect — never inherited from the fact that
    # the run started. A downgrade between diagnosis and send stops the send.
    async with org_scoped_session(org_id) as s:
        await set_org_context(s, org_id)
        try:
            await assert_entitled(s, org_id, grant.capability)
            await assert_agent_executable(s, org_id, instance["id"], worker=grant)
        except FeatureNotInPlan as exc:
            raise ToolStepError("not_entitled", capability=grant.capability) from exc
        except AgentNotExecutable as exc:
            raise ToolStepError("agent_not_executable", detail=exc.reason) from exc

    return ToolPrincipal(grant=grant, instance_id=instance["id"], manifest=manifest)


# ---- execution ------------------------------------------------------------------------


async def run_tool(
    org_id: UUID, run_id: UUID, instr: dict[str, Any], activation: dict[str, Any], *,
    redis: Redis | None = None,
) -> dict[str, Any]:
    """Execute one `tool_call` step and describe the outcome for the workflow engine.

    Returns a status dict rather than raising, because every outcome here is ordinary workflow
    control flow: `ok` advances, `pending` parks for approval, `failed` compensates. The two
    resolution failures (`input_unresolved`, and any authority failure) are deliberately *failures*
    and not silent skips — a recovery step that cannot identify its conversation must stop, not
    proceed with a plausible-looking guess.
    """
    from redis.asyncio import Redis as _Redis

    from core.common.config import get_settings
    from core.mediation import proxy
    from core.mediation.proxy import RunAborted, RunContext

    tool_name = str(instr["name"])
    try:
        params = resolve_inputs(instr.get("input_map", {}) or {}, activation)
        principal = await resolve_principal(org_id, run_id)
    except ToolStepError as exc:
        logger.warning("workflow.tool_call refused: %s %s", tool_name, exc.reason)
        return {"status": "failed", "tool": tool_name, "reason": exc.reason, **exc.detail}

    ctx = RunContext(
        org_id=org_id,
        # The workflow run *is* the run for mediation's purposes: violation counting, budget and
        # audit all attribute to the thing that actually decided to act.
        run_id=run_id,
        instance_id=principal.instance_id,
        manifest=principal.manifest,
        manifest_hash=manifest_module.manifest_hash(principal.manifest),
        approved=frozenset(instr.get("_approved") or ()),
    )
    redis = redis or _Redis.from_url(get_settings().redis_url)
    async with org_scoped_session(org_id) as s:
        await set_org_context(s, org_id)
        try:
            result = await proxy.call(ctx, tool_name, params, session=s, redis=redis)
            await s.commit()
        except RunAborted:
            await s.rollback()
            return {"status": "failed", "tool": tool_name, "reason": "run_aborted"}

    if result.pending is not None:
        return {"status": "pending", "tool": tool_name, "tier": result.pending.tier,
                "reason": result.pending.reason, "params": params,
                "capability": principal.grant.capability,
                "instance_id": str(principal.instance_id)}
    if not result.ok:
        err = result.error
        return {"status": "failed", "tool": tool_name,
                "reason": err.code if err else "tool_error",
                "message": err.message if err else None}
    return {"status": "ok", "tool": tool_name, "output": result.output,
            "audit_id": str(result.audit_id) if result.audit_id else None}
