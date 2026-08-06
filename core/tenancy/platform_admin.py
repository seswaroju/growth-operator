"""Platform-admin authority + the cross-tenant operator session (support-tickets track).

The Growth Operator control plane needs to read/act across *all* tenants, which cuts against the
strict per-org RLS everything else relies on. This module is the single, deliberate, audited path
for that:

- The **sole authority** for platform-admin is the `platform_admins` allowlist (migration 018) —
  a set of user ids. It is intentionally NOT the org-scoped `founder` role, so that no per-store
  role can ever confer cross-tenant reach (that would be a tenant-isolation escalation).
- `get_admin_db` yields a session that sets the transaction-local `app.platform_admin='on'` GUC
  (the RLS escape hatch in migration 018) **only after** verifying the caller is allowlisted. With
  no flag, every table stays strictly org-scoped, so absence fails closed. The flag is set with
  `set_config(..., true)` (== `SET LOCAL`), never session-level, so it can't leak across a pooled
  connection (same rule as `app.org_id`).

See project-management/DECISIONS.md 2026-08-05.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.config import Settings, get_settings
from core.common.db import get_sessionmaker
from core.tenancy.deps import get_current_auth


async def log_platform_access(
    session: AsyncSession,
    *,
    actor_user_id: str | UUID,
    action: str,
    target_org_id: str | UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one cross-tenant operator action to the append-only `platform_access_log` (migration
    019) — the admin-plane audit trail. Runs in the caller's `get_admin_db` transaction, so the log
    commits atomically with the action it records; a rolled-back request logs nothing."""
    await session.execute(
        text(
            "INSERT INTO platform_access_log (actor_user_id, action, target_org_id, detail) "
            "VALUES (:actor, :action, :org, CAST(:detail AS jsonb))"
        ),
        {"actor": str(actor_user_id), "action": action,
         "org": str(target_org_id) if target_org_id else None,
         "detail": json.dumps(detail or {})},
    )


async def is_platform_admin(session: AsyncSession, user_id: str | UUID) -> bool:
    """True iff `user_id` is a *currently-valid* platform admin: on the allowlist and not expired.
    An `expires_at` in the past means NOT an admin (fail closed). `platform_admins` is not
    org-scoped (no RLS), so this resolves regardless of the session's tenant context."""
    row = await session.execute(
        text(
            "SELECT 1 FROM platform_admins "
            "WHERE user_id = :u AND (expires_at IS NULL OR expires_at > now())"
        ),
        {"u": str(user_id)},
    )
    return row.first() is not None


@asynccontextmanager
async def admin_scoped_session(user_id: str | UUID | None = None) -> AsyncIterator[AsyncSession]:
    """Worker/test cross-tenant operator scope: a session with `app.platform_admin='on'` and no org.

    The caller is responsible for having established admin authority first (for HTTP, `get_admin_db`
    does the allowlist check). Use only for verified-operator work — never in a tenant request path.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            if user_id is not None:
                await session.execute(
                    text("SELECT set_config('app.user_id', :v, true)"), {"v": str(user_id)}
                )
            await session.execute(text("SELECT set_config('app.platform_admin', 'on', true)"))
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_admin_db(
    request: Request, settings: Settings = Depends(get_settings)
) -> AsyncIterator[AsyncSession]:
    """Operator-route DB dependency: verify the Bearer caller is on the platform_admins allowlist,
    then yield a cross-tenant session (`app.platform_admin='on'`).

    401 for a missing/invalid token (via `get_current_auth`); 403 for an authenticated non-admin.
    The admin flag is set only on the allowlisted path, so a non-admin's session never carries it.
    """
    current = get_current_auth(request, settings)  # raises 401 on missing/invalid token
    user_id = str(current.user_id)
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            await session.execute(
                text("SELECT set_config('app.user_id', :v, true)"), {"v": user_id}
            )
            if not await is_platform_admin(session, user_id):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "platform admin required")
            await session.execute(text("SELECT set_config('app.platform_admin', 'on', true)"))
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
