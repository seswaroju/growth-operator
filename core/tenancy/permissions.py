"""Tenant-plane RBAC — the single source of truth for a store's roles (Phase 1.1).

Four graduated roles, scoped to a membership (`user_orgs.role`, carried in the JWT `roles` claim):

- `owner`   — everything in their store (settings, billing, team, all trackers).
- `manager` — runs the business: approvals, catalog, conversations, customers, campaigns, insights,
              and inviting staff — but NOT org settings, billing, or member management.
- `staff`   — day-to-day: handle conversations + approvals, read catalog/customers, see insights.
- `viewer`  — read-only dashboards.

Enforcement is constant-based (the `@requires` dependency resolves perms from `ROLE_PERMISSIONS` —
no per-request DB I/O); the `roles`/`permissions`/`role_permissions` tables (migration 003, reseeded
in 021) are seeded FROM this file and a drift test (`test_rbac_seed`) asserts they still agree.

This is the **tenant plane** only. Cross-tenant operator power lives in a SEPARATE plane
(`core.tenancy.platform_admin` + `platform_permissions`) — deliberately, so no store role can ever
confer cross-tenant reach. (The retired `founder` role + `platform:admin` permission used to blur
that line; removed in Phase 1.1 — DECISIONS 2026-08-06.)

Permission names are `resource:action`, platform-generic — no industry nouns (Rule Zero). New
permissions are defined ahead of their feature and stay deny-by-default until an endpoint uses them.
"""

from __future__ import annotations

from collections.abc import Iterable

# ---- Roles (ranked: an inviter can grant at or below their own rank) --------

ROLE_OWNER = "owner"
ROLE_MANAGER = "manager"
ROLE_STAFF = "staff"
ROLE_VIEWER = "viewer"

ALL_ROLES: frozenset[str] = frozenset({ROLE_OWNER, ROLE_MANAGER, ROLE_STAFF, ROLE_VIEWER})

ROLE_RANK: dict[str, int] = {ROLE_OWNER: 3, ROLE_MANAGER: 2, ROLE_STAFF: 1, ROLE_VIEWER: 0}

# ---- Permissions (resource:action) -----------------------------------------

APPROVALS_READ = "approvals:read"
APPROVALS_RESOLVE = "approvals:resolve"
CATALOG_READ = "catalog:read"
CATALOG_WRITE = "catalog:write"
CONVERSATIONS_READ = "conversations:read"
CONVERSATIONS_RESPOND = "conversations:respond"
CUSTOMERS_READ = "customers:read"
CUSTOMERS_WRITE = "customers:write"
CAMPAIGNS_READ = "campaigns:read"
CAMPAIGNS_SEND = "campaigns:send"
INSIGHTS_READ = "insights:read"
MEMBERS_INVITE = "members:invite"
MEMBERS_MANAGE = "members:manage"
ORG_MANAGE = "org:manage"
BILLING_MANAGE = "billing:manage"

ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        APPROVALS_READ, APPROVALS_RESOLVE,
        CATALOG_READ, CATALOG_WRITE,
        CONVERSATIONS_READ, CONVERSATIONS_RESPOND,
        CUSTOMERS_READ, CUSTOMERS_WRITE,
        CAMPAIGNS_READ, CAMPAIGNS_SEND,
        INSIGHTS_READ,
        MEMBERS_INVITE, MEMBERS_MANAGE,
        ORG_MANAGE, BILLING_MANAGE,
    }
)

# ---- Role → permission grants ----------------------------------------------

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    # owner: everything in their store.
    ROLE_OWNER: ALL_PERMISSIONS,
    # manager: run the business, invite staff — but not settings/billing/member-management.
    ROLE_MANAGER: frozenset(
        {
            APPROVALS_READ, APPROVALS_RESOLVE,
            CATALOG_READ, CATALOG_WRITE,
            CONVERSATIONS_READ, CONVERSATIONS_RESPOND,
            CUSTOMERS_READ, CUSTOMERS_WRITE,
            CAMPAIGNS_READ, CAMPAIGNS_SEND,
            INSIGHTS_READ,
            MEMBERS_INVITE,
        }
    ),
    # staff: handle conversations + approvals, read catalog/customers, see insights.
    ROLE_STAFF: frozenset(
        {
            APPROVALS_READ, APPROVALS_RESOLVE,
            CATALOG_READ,
            CONVERSATIONS_READ, CONVERSATIONS_RESPOND,
            CUSTOMERS_READ,
            INSIGHTS_READ,
        }
    ),
    # viewer: read-only dashboards.
    ROLE_VIEWER: frozenset(
        {
            APPROVALS_READ,
            CATALOG_READ,
            CONVERSATIONS_READ,
            CUSTOMERS_READ,
            CAMPAIGNS_READ,
            INSIGHTS_READ,
        }
    ),
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


def _max_rank(roles: Iterable[str]) -> int:
    return max((ROLE_RANK.get(r, -1) for r in roles), default=-1)


def can_grant_role(actor_roles: Iterable[str], target_role: str) -> bool:
    """An actor may assign `target_role` only if it is a real role at or below the actor's own rank
    — you can never grant a role more powerful than your own."""
    return target_role in ALL_ROLES and ROLE_RANK.get(target_role, 99) <= _max_rank(actor_roles)


def assignable_roles(actor_roles: Iterable[str]) -> frozenset[str]:
    """The set of roles this actor is allowed to grant (at or below their own rank)."""
    ceiling = _max_rank(actor_roles)
    return frozenset(r for r in ALL_ROLES if ROLE_RANK[r] <= ceiling)
