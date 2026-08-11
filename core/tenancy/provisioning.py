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
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import service as billing
from core.channels.email import EmailClient
from core.common.errors import GrowthOperatorError
from core.tenancy import repository
from core.tenancy.auth import OtpChannel

logger = logging.getLogger("core.tenancy.provisioning")


@dataclass(frozen=True)
class ProvisionResult:
    org_id: UUID
    owner_id: UUID
    owner_existed: bool  # the owner already had an account (runs another store) → reused
    plan_id: UUID


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
    #    so an invalid plan writes nothing (atomic by construction).
    active = (
        await session.execute(
            text("SELECT 1 FROM billing_plans WHERE id = :p AND active = true"),
            {"p": str(plan_id)},
        )
    ).first()
    if active is None:
        raise GrowthOperatorError("config_schema_violation", "unknown or inactive plan")

    # 2. The org (no RLS on `organizations`).
    org_id = await repository.insert_organization(
        session, name=name, vertical=vertical, country=country, timezone=timezone)

    # 3. The owner user — reused if the email already has an account (a multi-store owner).
    existed = (
        await session.execute(text("SELECT 1 FROM users WHERE email = :e"), {"e": owner_email})
    ).first() is not None
    owner_id = await repository.get_or_create_user(session, OtpChannel.EMAIL, owner_email)

    # 4. Org-scoped inserts need `app.org_id` == this org (the FORCE-RLS INSERT check).
    await repository.set_org_context(session, org_id)
    await repository.insert_user_org(session, user_id=owner_id, org_id=org_id, role="owner")

    # 5. The subscription (`assign_subscription` sets org context itself).
    await billing.assign_subscription(session, org_id, plan_id)

    return ProvisionResult(
        org_id=org_id, owner_id=owner_id, owner_existed=existed, plan_id=plan_id)


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
