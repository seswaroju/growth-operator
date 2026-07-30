"""RBAC permission constants — the single source of truth for MVP roles (MVP-015).

Three roles only (auth-rbac spec §RBAC): `owner` (everything in their org), `staff`
(read-only in MVP — "read + approvals if delegated" is post-MVP), `founder` (platform ops,
everything, cross-org via explicit audited paths only). Enforcement is constant-based (the
`@requires` dependency resolves perms from `ROLE_PERMISSIONS` — no per-request DB I/O); the
`roles`/`permissions`/`role_permissions` tables in migration 003 are seeded FROM this file
and a drift test asserts they still agree.

Permission names are `resource:action`, platform-generic — no industry nouns (Rule Zero).
This file is append-only in MVP; removing a permission needs a decision-log entry.
"""

from __future__ import annotations

from collections.abc import Iterable

# ---- Roles -----------------------------------------------------------------

ROLE_OWNER = "owner"
ROLE_STAFF = "staff"
ROLE_FOUNDER = "founder"

ALL_ROLES: frozenset[str] = frozenset({ROLE_OWNER, ROLE_STAFF, ROLE_FOUNDER})

# ---- Permissions (resource:action) -----------------------------------------

APPROVALS_READ = "approvals:read"
APPROVALS_RESOLVE = "approvals:resolve"
CATALOG_READ = "catalog:read"
CATALOG_WRITE = "catalog:write"
CAMPAIGNS_SEND = "campaigns:send"
MEMBERS_INVITE = "members:invite"
ORG_MANAGE = "org:manage"
PLATFORM_ADMIN = "platform:admin"

ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        APPROVALS_READ,
        APPROVALS_RESOLVE,
        CATALOG_READ,
        CATALOG_WRITE,
        CAMPAIGNS_SEND,
        MEMBERS_INVITE,
        ORG_MANAGE,
        PLATFORM_ADMIN,
    }
)

# ---- Role → permission grants ----------------------------------------------
# owner: everything in their org except platform admin.
# staff: read-only in MVP.
# founder: everything, including cross-org platform admin.

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    ROLE_OWNER: frozenset(
        {
            APPROVALS_READ,
            APPROVALS_RESOLVE,
            CATALOG_READ,
            CATALOG_WRITE,
            CAMPAIGNS_SEND,
            MEMBERS_INVITE,
            ORG_MANAGE,
        }
    ),
    ROLE_STAFF: frozenset({APPROVALS_READ, CATALOG_READ}),
    ROLE_FOUNDER: ALL_PERMISSIONS,
}


def permissions_for(roles: Iterable[str]) -> frozenset[str]:
    """Union of the permissions granted by every role in `roles` (unknown roles → none)."""
    granted: set[str] = set()
    for role in roles:
        granted |= ROLE_PERMISSIONS.get(role, frozenset())
    return frozenset(granted)


def has_permission(roles: Iterable[str], permission: str) -> bool:
    """True iff any of `roles` grants `permission`."""
    return permission in permissions_for(roles)
