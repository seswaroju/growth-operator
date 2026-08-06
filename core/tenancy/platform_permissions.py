"""Platform-plane RBAC — Growth Operator operator roles (Phase 1.2).

A **separate** authorization namespace from the tenant plane (`core.tenancy.permissions`), on
purpose: a tenant role can never grant a platform permission and vice-versa (verified by a
plane-separation test). Keyed off `platform_admins.role` (migration 022), resolved on the audited
`require_platform(...)` path in `core.tenancy.platform_admin`.

Roles (graduated):
- `dev`     — engineering: everything, including impersonation + debug internals.
- `admin`   — ops/account lead: manage tenants + operators + tickets + insights; no impersonate.
- `staff`   — support/success: handle tickets, read tenant health + insights.
- `analyst` — read-only: tenant health + insights, nothing else.

Permission names are `platform.<resource>:<action>` — the `platform.` prefix makes the plane
unmistakable and keeps them disjoint from tenant `resource:action` permissions.
"""

from __future__ import annotations

from collections.abc import Iterable

# ---- Platform roles --------------------------------------------------------

PLATFORM_DEV = "dev"
PLATFORM_ADMIN_ROLE = "admin"
PLATFORM_STAFF = "staff"
PLATFORM_ANALYST = "analyst"

PLATFORM_ROLES: frozenset[str] = frozenset(
    {PLATFORM_DEV, PLATFORM_ADMIN_ROLE, PLATFORM_STAFF, PLATFORM_ANALYST}
)

# ---- Platform permissions (platform.resource:action) -----------------------

PLATFORM_TICKETS_READ = "platform.tickets:read"
PLATFORM_TICKETS_RESOLVE = "platform.tickets:resolve"
PLATFORM_TENANTS_READ = "platform.tenants:read"
PLATFORM_TENANTS_MANAGE = "platform.tenants:manage"
PLATFORM_INSIGHTS_READ = "platform.insights:read"
PLATFORM_ADMINS_MANAGE = "platform.admins:manage"
PLATFORM_IMPERSONATE = "platform.impersonate"
PLATFORM_DEBUG = "platform.debug"

PLATFORM_ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        PLATFORM_TICKETS_READ, PLATFORM_TICKETS_RESOLVE,
        PLATFORM_TENANTS_READ, PLATFORM_TENANTS_MANAGE,
        PLATFORM_INSIGHTS_READ,
        PLATFORM_ADMINS_MANAGE,
        PLATFORM_IMPERSONATE, PLATFORM_DEBUG,
    }
)

# ---- Role → permission grants ----------------------------------------------

PLATFORM_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    # dev: everything, including impersonation + debug.
    PLATFORM_DEV: PLATFORM_ALL_PERMISSIONS,
    # admin: run the operation — tenants, operators, tickets, insights — but not impersonate/debug.
    PLATFORM_ADMIN_ROLE: frozenset(
        {
            PLATFORM_TICKETS_READ, PLATFORM_TICKETS_RESOLVE,
            PLATFORM_TENANTS_READ, PLATFORM_TENANTS_MANAGE,
            PLATFORM_INSIGHTS_READ,
            PLATFORM_ADMINS_MANAGE,
        }
    ),
    # staff: support/success — handle tickets, read tenant health + insights.
    PLATFORM_STAFF: frozenset(
        {PLATFORM_TICKETS_READ, PLATFORM_TICKETS_RESOLVE, PLATFORM_TENANTS_READ,
         PLATFORM_INSIGHTS_READ}
    ),
    # analyst: read-only analytics.
    PLATFORM_ANALYST: frozenset({PLATFORM_TENANTS_READ, PLATFORM_INSIGHTS_READ}),
}


def platform_permissions_for(role: str) -> frozenset[str]:
    """Permissions granted by a platform `role` (unknown role → none)."""
    return PLATFORM_ROLE_PERMISSIONS.get(role, frozenset())


def platform_has_permission(role: str, permission: str) -> bool:
    """True iff `role` grants `permission` on the platform plane."""
    return permission in platform_permissions_for(role)


def platform_permissions_for_roles(roles: Iterable[str]) -> frozenset[str]:
    """Union of the platform permissions for every role in `roles`."""
    granted: set[str] = set()
    for role in roles:
        granted |= platform_permissions_for(role)
    return frozenset(granted)
