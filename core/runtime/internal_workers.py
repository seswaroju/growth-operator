"""Internal capability workers — execution authority for non-commercial archetypes (PILOT-1C).

A customer buys *Ghost Lead Recovery*; they do not buy a "Nurture Agent". The reasoning that
implements a purchased capability therefore needs execution authority, but `nurture` is
`partial`/`internal` in the PLAN-1 catalog and appears in no plan, so PLAN-5's
`assert_agent_executable` correctly refuses it.

Rather than a caller-asserted escape hatch — `via_capability="ghost_recovery"` would let whoever
calls the function name their own authority — authority is **declared here in code** and **bound to
a trusted workflow identity**:

    capability → archetype → task → workflow_key

Minting requires all of: the capability is currently entitled · the archetype and task are
registered for it · the run originates from a **certified first-party pack workflow** whose key
matches · that definition is active · the tenant has the installed-pack binding/instance · the
instance is operationally executable. The workflow key is read from the persisted workflow
definition, never from an event payload, HTTP body, model output or a DSL field — otherwise a
future custom workflow naming `nurture/ghost_diagnosis` would turn this registry into an escalation
primitive.

The granted tuple is persisted on the agent run so start, resume, approval-resume and the mediation
boundary all re-verify the *same* authority. A downgrade or cancellation therefore stops execution
at the next check rather than at the next restart.

This does not widen tool access: the PLAN-5 tool-capability gate applies independently, and a worker
still reaches external effects only through the mediation proxy.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InternalWorkerGrant:
    capability: str      # must be entitled at execution time
    archetype: str       # an internal (non-sellable) archetype
    task: str            # the exact task this grant authorises
    workflow_key: str    # the certified pack workflow allowed to mint it


#: The complete set. One entry for the pilot — recovery diagnosis. `reason_conditioned_recovery` is
#: deliberately absent: PILOT-1C sends a deterministic approved template, so no generative worker is
#: authorised to produce customer-facing copy.
GRANTS: tuple[InternalWorkerGrant, ...] = (
    InternalWorkerGrant(
        capability="ghost_recovery",
        archetype="nurture",
        task="ghost_diagnosis",
        workflow_key="silent_lead_reactivation",
    ),
)


class InternalWorkerDenied(Exception):
    """No grant covers this (workflow, archetype, task), or one of its conditions failed."""

    def __init__(self, reason: str, **detail: object):
        self.reason = reason
        self.detail = detail
        super().__init__(f"internal worker denied: {reason}")


def find_grant(
    *, workflow_key: str | None, archetype: str, task: str
) -> InternalWorkerGrant | None:
    """The grant for this exact triple, or None. An unknown workflow key never matches."""
    if not workflow_key:
        return None
    for g in GRANTS:
        if g.workflow_key == workflow_key and g.archetype == archetype and g.task == task:
            return g
    return None


def is_internal_archetype(archetype: str) -> bool:
    """True when some grant covers this archetype — i.e. it may run as an internal worker at all."""
    return any(g.archetype == archetype for g in GRANTS)


def validate_registry() -> list[str]:
    """Structural invariants, checked in CI.

    The important one: a grant may only cover an archetype that is **not** commercially sellable.
    A sellable agent must go through the plan, never through this side door."""
    from core.tenancy.capabilities import by_key

    problems: list[str] = []
    seen: set[tuple[str, str, str, str]] = set()
    for g in GRANTS:
        key = (g.capability, g.archetype, g.task, g.workflow_key)
        if key in seen:
            problems.append(f"duplicate grant {key}")
        seen.add(key)

        cap = by_key(g.capability)
        if cap is None:
            problems.append(f"{g.capability}: not a canonical capability")
        elif not cap.runtime_grantable:
            problems.append(f"{g.capability}: not an authorization boundary")

        agent = by_key(f"agent.{g.archetype}")
        if agent is None:
            problems.append(f"agent.{g.archetype}: not in the capability catalog")
        elif agent.commercial_visibility in ("public", "public_beta"):
            problems.append(
                f"agent.{g.archetype} is sellable — it must be bought through a plan, "
                "not authorised as an internal worker")
        for field, value in (("task", g.task), ("workflow_key", g.workflow_key)):
            if not value or not value.replace("_", "").replace(".", "").isalnum():
                problems.append(f"{g.archetype}: implausible {field} {value!r}")
    return problems
