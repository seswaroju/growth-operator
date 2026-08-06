"""Support-ticket HTTP routes (support-tickets track).

- Owner routes (`/v1/support/tickets`) run under the tenant session (`get_db`), so a store owner
  only ever sees/creates tickets in their own org.
- Operator routes (`/v1/admin/support/tickets`) run under the audited platform-admin session
  (`get_admin_db`), the single cross-tenant path — a non-allowlisted caller gets 403.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.common.config import Settings, get_settings
from core.support import service
from core.support.schemas import (
    AdminTicketOut,
    Priority,
    Status,
    TicketCreate,
    TicketOut,
    TicketUpdate,
)
from core.tenancy.deps import CurrentAuth, get_current_auth
from core.tenancy.middleware import get_db
from core.tenancy.platform_admin import get_admin_db


def require_admin_plane_enabled(settings: Settings = Depends(get_settings)) -> None:
    """Gate the whole operator plane behind `admin_plane_enabled` (default off). When disabled we
    404 — before auth, for everyone — so the admin API's existence isn't even revealed."""
    if not settings.admin_plane_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")


owner_router = APIRouter(prefix="/v1/support/tickets", tags=["support"])
# The router-level dependency runs before the route's own (auth) dependencies, so a disabled plane
# 404s ahead of any 401/403 — the endpoint looks like it doesn't exist.
admin_router = APIRouter(
    prefix="/v1/admin/support/tickets", tags=["support-admin"],
    dependencies=[Depends(require_admin_plane_enabled)],
)


def _require_org(current: CurrentAuth) -> UUID:
    if current.org_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no organization context")
    return current.org_id


# ---- store-owner: report + track their own issues -----------------------------------------------
@owner_router.post("", status_code=status.HTTP_201_CREATED, response_model=TicketOut,
                   summary="Report an issue")
async def create_ticket(
    body: TicketCreate,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    org_id = _require_org(current)
    return await service.raise_ticket(
        session, org_id, subject=body.subject, description=body.description,
        category=body.category, severity=body.severity, raised_by=current.user_id,
    )


@owner_router.get("", response_model=list[TicketOut], summary="My tickets")
async def list_my_tickets(
    status_filter: Status | None = None,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    org_id = _require_org(current)
    return await service.list_own(session, org_id, status=status_filter)


@owner_router.get("/{ticket_id}", response_model=TicketOut, summary="One of my tickets")
async def get_my_ticket(
    ticket_id: UUID,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    org_id = _require_org(current)
    ticket = await service.get_own(session, org_id, ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found")
    return ticket


# ---- Growth Operator: the cross-tenant operator queue -------------------------------------------
@admin_router.get("", response_model=list[AdminTicketOut],
                  summary="Operator queue (all tenants)")
async def admin_list_tickets(
    status_filter: Status | None = None,
    priority: Priority | None = None,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(get_admin_db),
) -> list[dict[str, Any]]:
    return await service.list_all(
        session, actor_user_id=current.user_id, status=status_filter, priority=priority)


@admin_router.patch("/{ticket_id}", response_model=AdminTicketOut,
                    summary="Triage / resolve a ticket")
async def admin_update_ticket(
    ticket_id: UUID,
    body: TicketUpdate,
    current: CurrentAuth = Depends(get_current_auth),
    session: AsyncSession = Depends(get_admin_db),
) -> dict[str, Any]:
    if body.priority is None and body.status is None and body.resolution_note is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "provide at least one of priority, status, resolution_note",
        )
    try:
        await service.update_ticket(
            session, ticket_id, actor_id=current.user_id,
            priority=body.priority, status=body.status, resolution_note=body.resolution_note,
        )
    except service.TicketNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found") from exc
    fresh = await service.get_admin(session, ticket_id)
    assert fresh is not None  # just updated it in this transaction
    return fresh
