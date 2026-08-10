"""Owner-built activation + trust ledger (MVP-073g — closes the workflow-engine initiative).

Owner-authored workflows (MVP-073e) are drafts and cannot self-activate. Per the spec
(`docs/21-platform/workflow-engine.md` §Builder / §Simulation):

- **first activation is gated** — `request_activation` runs a **simulation** (MVP-073d) and raises a
  **tier-2 approval** with the report attached; only an approve flips the draft to `active`;
- **earned autonomy** — owner-built runs are held at a **tier-2 floor** (max approval) until the
  definition accrues `TRUST_THRESHOLD` clean (completed) runs, then it earns normal autonomy.

Enforcement points: a draft never routes (so it can't run before activation), and once active the
`tier_floor` from `owner_trust_status` is what the mediation/approval boundary applies to the run's
actions. This module owns the activation approval + the trust computation; the runtime already gates
every real send behind the simulated provider + approvals meanwhile.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.tenancy.repository import set_org_context
from core.workflows import simulate as simulate_mod
from core.workflows import store

ACTIVATE_ACTION = "workflow.activate"
ACTIVATE_TIER = 2
TRUST_THRESHOLD = 50


class ActivationError(ValueError):
    """Activation requested on a definition that can't be activated (not owner-built / active)."""


def _trust(clean_runs: int) -> dict[str, Any]:
    """Pure trust verdict for a run count: earned once `clean_runs >= TRUST_THRESHOLD`; until then
    the run's actions sit at a tier-2 floor (max approval)."""
    earned = clean_runs >= TRUST_THRESHOLD
    return {"clean_runs": clean_runs, "threshold": TRUST_THRESHOLD,
            "earned": earned, "tier_floor": None if earned else ACTIVATE_TIER}


async def owner_trust_status(
    session: AsyncSession, org_id: UUID, definition_id: UUID
) -> dict[str, Any]:
    """Trust status for an owner-built definition, keyed on its completed (clean) run count."""
    await set_org_context(session, org_id)
    clean = int((await session.execute(
        text("SELECT count(*) FROM workflow_runs WHERE org_id = :o AND definition_id = :d "
             "AND status = 'completed'"),
        {"o": str(org_id), "d": str(definition_id)})).scalar_one())
    return _trust(clean)


async def request_activation(
    session: AsyncSession, org_id: UUID, definition_id: UUID, *, window_days: int = 30
) -> dict[str, Any]:
    """Run a simulation and raise a tier-2 activation approval (report attached). Returns the
    approval id + simulation summary + trust; the draft stays draft until the approval resolves."""
    await set_org_context(session, org_id)
    row = (await session.execute(
        text("SELECT workflow_key, origin, status FROM workflow_definitions "
             "WHERE id = :d AND org_id = :o"),
        {"d": str(definition_id), "o": str(org_id)})).mappings().first()
    if row is None:
        raise KeyError(f"unknown definition {definition_id}")
    if row["origin"] != "owner_built":
        raise ActivationError("only owner-built definitions use the activation flow")
    if row["status"] == "active":
        raise ActivationError("definition is already active")

    report = await simulate_mod.simulate(session, org_id, definition_id, window_days=window_days)
    trust = await owner_trust_status(session, org_id, definition_id)

    from core.approvals.service import create_approval
    approval_id = await create_approval(
        session, org_id, action_type=ACTIVATE_ACTION, tier=ACTIVATE_TIER,
        payload={"definition_id": str(definition_id), "workflow_key": row["workflow_key"],
                 "simulation": {k: report[k] for k in
                                ("candidates", "would_have_fired", "guard_blocks",
                                 "estimated_cost_minor")},
                 "trust": trust})
    return {"approval_id": str(approval_id), "simulation": report, "trust": trust}


async def apply_activation_decision(
    session: AsyncSession, org_id: UUID, definition_id: UUID, approved: bool
) -> None:
    """Resolve an activation approval: approve → activate the draft; reject → leave it a draft."""
    if approved:
        await store.activate(session, org_id, definition_id)
