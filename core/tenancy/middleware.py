"""Tenant context: per-request/-job transaction scoping via SET LOCAL (MVP-016).

Every org-scoped query must run inside a transaction that has `app.org_id` (and
`app.user_id`) set, so the RLS policies (migration 002+) resolve to the caller's tenant.
`set_config(name, value, true)` is transaction-local (== `SET LOCAL`), which is safe under
PgBouncer transaction pooling; session-level `SET` is banned (scripts/guards.py) because a
leaked GUC on a pooled connection would cross tenants.

- `get_db` — FastAPI dependency for authed routes: opens a session, sets the tenant GUCs
  from the request's verified access token, yields, commits. No token → no context (fail
  closed: RLS returns zero rows). It decodes the token itself rather than relying on a
  BaseHTTPMiddleware + contextvar (those don't reliably propagate into the endpoint).
- `org_scoped_session` — the worker/job equivalent: a job carries its `org_id`, opens a
  session scoped to it, and never spans tenants in one transaction.

`org_id` is also stamped onto `telemetry.org_id_var` for the duration so log lines during
the request/job carry it (MVP-006).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, Request
from jose import JWTError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.config import Settings, get_settings
from core.common.db import get_sessionmaker
from core.common.telemetry import org_id_var
from core.tenancy import auth


def _tenant_from_request(request: Request, settings: Settings) -> tuple[str | None, str | None]:
    """Best-effort (user_id, org_id) from a Bearer access token. Never raises — a route's
    own auth dependency is what rejects bad/absent tokens; here a bad token just means no
    tenant context (and therefore, under RLS, no rows)."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None, None
    try:
        claims = auth.decode_token(token, settings.jwt_secret)
    except JWTError:
        return None, None
    if claims.get("type") != "access":
        return None, None
    sub, org = claims.get("sub"), claims.get("org_id")
    return (str(sub) if sub else None), (str(org) if org else None)


async def _set_tenant_context(
    session: AsyncSession, *, user_id: str | None, org_id: str | None
) -> None:
    if user_id is not None:
        await session.execute(
            text("SELECT set_config('app.user_id', :v, true)"), {"v": user_id}
        )
    if org_id is not None:
        await session.execute(
            text("SELECT set_config('app.org_id', :v, true)"), {"v": org_id}
        )


async def get_db(
    request: Request, settings: Settings = Depends(get_settings)
) -> AsyncIterator[AsyncSession]:
    """Authed-route DB dependency: one tenant-scoped transaction per request."""
    user_id, org_id = _tenant_from_request(request, settings)
    otel_token = org_id_var.set(org_id) if org_id else None
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            await _set_tenant_context(session, user_id=user_id, org_id=org_id)
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            if otel_token is not None:
                org_id_var.reset(otel_token)


@asynccontextmanager
async def org_scoped_session(
    org_id: UUID | str, user_id: UUID | str | None = None
) -> AsyncIterator[AsyncSession]:
    """Worker/job tenant scope: a session bound to one org's context.

    Fan-out jobs (e.g. a per-tenant digest) must open ONE of these PER org — never a single
    transaction spanning tenants (multi-tenant-rls.md).
    """
    otel_token = org_id_var.set(str(org_id))
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            await _set_tenant_context(
                session,
                user_id=str(user_id) if user_id is not None else None,
                org_id=str(org_id),
            )
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            org_id_var.reset(otel_token)
