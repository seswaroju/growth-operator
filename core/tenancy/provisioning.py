"""Store provisioning for the operator control plane (CP-2).

The GO operator creates a store from web-ops. `provision_store` composes the primitives into
one **atomic** provision — a new org, the owner user (reused if the email already has an account, so
one owner can run several stores), the owner membership, and the plan subscription — in a single
transaction, so a half-created store never lingers (the operator dep rolls back on any error). The
welcome email is best-effort and sent by the caller after the provision.

Rule Zero: `core/` never names a vertical — the store's vertical comes from the request (or the
`organizations.vertical` column default when omitted), never a literal here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import service as billing
from core.channels.email import EmailClient
from core.common.errors import GrowthOperatorError
from core.packs.installer import install, resolve_pack_dir
from core.tenancy import repository
from core.tenancy.auth import OtpChannel
from core.tenancy.middleware import org_scoped_session

logger = logging.getLogger("core.tenancy.provisioning")


@dataclass(frozen=True)
class ProvisionResult:
    org_id: UUID
    owner_id: UUID
    owner_existed: bool  # the owner already had an account (runs another store) → reused
    plan_id: UUID
    pack_dir: Path  # the store's vertical pack to install after commit (CP-2b)
    agent_slugs: list[str] = field(default_factory=list)  # archetypes the plan switches on


async def provision_store(
    session: AsyncSession, *, name: str, owner_email: str, plan_id: UUID,
    vertical: str | None = None, country: str = "IN", timezone: str = "Asia/Kolkata",
) -> ProvisionResult:
    """Create a store atomically: org + owner + owner membership + active plan subscription.

    Raises `GrowthOperatorError('config_schema_violation')` if the plan is missing or inactive — and
    because that check runs *before* any write, an invalid plan creates nothing. The caller commits,
    then sends the welcome email.
    """
    # 1. The plan must exist AND be active — you can't put a store on a retired plan. Checked first
    #    so an invalid plan writes nothing (atomic by construction). Read its config to know which
    #    agents the plan switches on (CP-2b).
    plan = (
        await session.execute(
            text("SELECT config FROM billing_plans WHERE id = :p AND active = true"),
            {"p": str(plan_id)},
        )
    ).mappings().first()
    if plan is None:
        raise GrowthOperatorError("config_schema_violation", "unknown or inactive plan")
    agents = list((plan["config"] or {}).get("agents") or [])

    # 2. The org (no RLS on `organizations`).
    org_id = await repository.insert_organization(
        session, name=name, vertical=vertical, country=country, timezone=timezone)

    # 3. The store's vertical pack must exist (from the org's vertical — request or column default).
    #    Resolving it here (before commit) makes an unknown vertical fail-fast → nothing written.
    org_vertical = (
        await session.execute(
            text("SELECT vertical FROM organizations WHERE id = :id"), {"id": str(org_id)})
    ).scalar_one()
    try:
        pack_dir = resolve_pack_dir(str(org_vertical))
    except Exception as exc:  # noqa: BLE001 — unknown/invalid vertical → 404, nothing committed
        raise GrowthOperatorError(
            "config_schema_violation", f"no vertical pack: {org_vertical}") from exc

    # 4. The owner user — reused if the email already has an account (a multi-store owner).
    existed = (
        await session.execute(text("SELECT 1 FROM users WHERE email = :e"), {"e": owner_email})
    ).first() is not None
    owner_id = await repository.get_or_create_user(session, OtpChannel.EMAIL, owner_email)

    # 5. Org-scoped inserts need `app.org_id` == this org (the FORCE-RLS INSERT check).
    await repository.set_org_context(session, org_id)
    await repository.insert_user_org(session, user_id=owner_id, org_id=org_id, role="owner")

    # 6. The subscription (`assign_subscription` sets org context itself).
    await billing.assign_subscription(session, org_id, plan_id)

    return ProvisionResult(
        org_id=org_id, owner_id=owner_id, owner_existed=existed, plan_id=plan_id,
        pack_dir=pack_dir, agent_slugs=agents)


async def activate_plan_agents(org_id: UUID, agent_slugs: list[str]) -> int:
    """Set the org's agent instances ACTIVE for exactly the archetypes the plan switches on (install
    leaves them paused). Returns the number activated (0 if the plan lists no agents)."""
    if not agent_slugs:
        return 0
    async with org_scoped_session(org_id) as s:
        rows = (
            await s.execute(
                text("UPDATE agent_instances ai SET status = 'active' "
                     "FROM agent_bindings ab JOIN agent_archetypes ar ON ar.id = ab.archetype_id "
                     "WHERE ai.binding_id = ab.id AND ai.org_id = :o AND ar.slug = ANY(:slugs) "
                     "AND ai.status <> 'active' RETURNING ai.id"),
                {"o": str(org_id), "slugs": agent_slugs})
        ).all()
        await s.commit()
        return len(rows)


async def finalize_store_setup(result: ProvisionResult) -> int:
    """After the store shell is committed: install its vertical pack (idempotent) and activate the
    plan's agents. `install` manages its own transaction, so this must run **after** the provision
    commits (the org must be visible). Returns the number of agents activated. Raises `InstallError`
    on a real install failure — the shell then exists with a failed install and can be retried
    (install is idempotent)."""
    await install(result.org_id, result.pack_dir, {})
    return await activate_plan_agents(result.org_id, result.agent_slugs)


async def send_welcome_email(owner_email: str, store_name: str) -> bool:
    """Best-effort welcome + setup email to the owner (gated `EmailClient` — simulated until live).
    Never raises: a failed email must not fail a provision. Returns True iff the send was ok."""
    from core.common.config import get_settings

    url = get_settings().owner_app_url
    where = f" at {url}" if url else ""
    try:
        result = await EmailClient().send(
            to=owner_email,
            subject="Your Growth Operator store is ready",
            text=(f"Welcome! Your store “{store_name}” is set up on Growth Operator.\n\n"
                  f"Sign in with this email address{where} to finish setup — no password needed; "
                  f"we'll send a one-time code."),
        )
    except Exception:  # noqa: BLE001 — email is best-effort; provisioning already succeeded
        logger.exception("welcome email failed for a newly provisioned store")
        return False
    return result.ok


# ---- Plan-change agent reconciliation (PLAN-5, BLOCKER #30) -------------------------------------


async def desired_plan_agents(
    session: AsyncSession, org_id: UUID, plan_id: UUID
) -> set[str]:
    """Archetypes a plan *intends* the store to have, before instances exist.

    Deliberately **not** derived from `EffectiveEntitlements.agents`: that already requires a
    tenant binding, so using it here would be circular — the very agent that needs provisioning is
    excluded because it has no instance yet. Composed instead from three independent facts:
    the plan's selection, the archetypes that exist, and the bindings the store's installed packs
    provide. Instance *status* never participates."""
    from core.tenancy.plan_config import parse_plan_config

    plan = (await session.execute(
        text("SELECT config FROM billing_plans WHERE id = :p"), {"p": str(plan_id)})
    ).mappings().first()
    if plan is None:
        return set()
    selected = set(parse_plan_config(plan["config"]).agents)
    if not selected:
        return set()
    await repository.set_org_context(session, org_id)
    supported = set((await session.execute(
        text("SELECT DISTINCT ar.slug FROM pack_installations pi "
             "JOIN agent_bindings ab ON ab.pack_id = pi.pack_id "
             "JOIN agent_archetypes ar ON ar.id = ab.archetype_id "
             "WHERE pi.org_id = :o AND pi.status = 'active'"),
        {"o": str(org_id)})).scalars().all())
    return selected & supported


async def reconcile_plan_agents(
    session: AsyncSession, org_id: UUID, new_plan_id: UUID
) -> dict[str, list[str]]:
    """Align agent *instances* with a newly assigned plan. Returns the audited delta.

    **Operational status is never rewritten because commercial entitlement changed.** PLAN-2 keeps
    those concepts apart, and collapsing them here would break twice: an operator's manual pause
    would be indistinguishable from a commercial removal, so a later re-upgrade could silently
    reactivate an agent the operator had deliberately stopped.

    Commercial authority is instead evaluated at execution time (`assert_agent_executable`), which
    means a removed agent cannot act even while its row still says `active`, and a delayed or
    failed reconciliation can never widen authority — it can only leave tidy-up pending.

    The one thing reconciliation *does* change is instance **existence**: a newly entitled
    archetype with no instance gets one in the pack's normal default state, so plan selection is
    never mistaken for manual activation.
    """
    desired = await desired_plan_agents(session, org_id, new_plan_id)
    # Set the context unconditionally: `desired_plan_agents` returns early when the plan selects no
    # agents, and `agent_instances` is FORCE-RLS — without this the "present" read would silently
    # come back empty and a downgrade would look like a no-op.
    await repository.set_org_context(session, org_id)
    present = set((await session.execute(
        text("SELECT ar.slug FROM agent_instances ai "
             "JOIN agent_bindings ab ON ab.id = ai.binding_id "
             "JOIN agent_archetypes ar ON ar.id = ab.archetype_id WHERE ai.org_id = :o"),
        {"o": str(org_id)})).scalars().all())

    created: list[str] = []
    for slug in sorted(desired - present):
        binding = (await session.execute(
            text("SELECT ab.id FROM pack_installations pi "
                 "JOIN agent_bindings ab ON ab.pack_id = pi.pack_id "
                 "JOIN agent_archetypes ar ON ar.id = ab.archetype_id "
                 "WHERE pi.org_id = :o AND pi.status = 'active' AND ar.slug = :s LIMIT 1"),
            {"o": str(org_id), "s": slug})).scalar_one_or_none()
        if binding is None:
            continue
        await session.execute(
            text("INSERT INTO agent_instances (org_id, binding_id, persona_name, status, "
                 "permission_manifest) SELECT :o, :b, ab.persona_default, 'paused', '{}'::jsonb "
                 "FROM agent_bindings ab WHERE ab.id = :b"),
            {"o": str(org_id), "b": str(binding)})
        created.append(slug)

    revoked = sorted(present - desired)
    return {
        "entitled": sorted(desired),
        "instances_created": created,
        "no_longer_entitled": revoked,
    }
