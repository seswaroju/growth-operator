"""Platform-plane RBAC grid + the tenant↔platform separation guarantee (Phase 1.2).

Pure, no DB. The separation test is the load-bearing one: the two planes' permission namespaces are
disjoint, so no tenant role can ever satisfy a platform permission check and vice-versa.
"""

from __future__ import annotations

import pytest

from core.tenancy import permissions as tenant
from core.tenancy import platform_permissions as P

MATRIX = [
    (P.PLATFORM_DEV, P.PLATFORM_IMPERSONATE, True),
    (P.PLATFORM_DEV, P.PLATFORM_DEBUG, True),
    (P.PLATFORM_ADMIN_ROLE, P.PLATFORM_TENANTS_MANAGE, True),
    (P.PLATFORM_ADMIN_ROLE, P.PLATFORM_ADMINS_MANAGE, True),
    (P.PLATFORM_ADMIN_ROLE, P.PLATFORM_TICKETS_RESOLVE, True),
    (P.PLATFORM_ADMIN_ROLE, P.PLATFORM_IMPERSONATE, False),   # admin can't impersonate
    (P.PLATFORM_ADMIN_ROLE, P.PLATFORM_DEBUG, False),
    (P.PLATFORM_STAFF, P.PLATFORM_TICKETS_RESOLVE, True),
    (P.PLATFORM_STAFF, P.PLATFORM_TENANTS_READ, True),
    (P.PLATFORM_STAFF, P.PLATFORM_TENANTS_MANAGE, False),     # staff can't manage tenants
    (P.PLATFORM_STAFF, P.PLATFORM_ADMINS_MANAGE, False),
    (P.PLATFORM_ANALYST, P.PLATFORM_TENANTS_READ, True),
    (P.PLATFORM_ANALYST, P.PLATFORM_INSIGHTS_READ, True),
    (P.PLATFORM_ANALYST, P.PLATFORM_TICKETS_READ, False),     # analyst: read-only, no tickets
    (P.PLATFORM_ANALYST, P.PLATFORM_TICKETS_RESOLVE, False),
]


@pytest.mark.parametrize(("role", "perm", "expected"), MATRIX)
def test_platform_permission_matrix(role: str, perm: str, expected: bool) -> None:
    assert P.platform_has_permission(role, perm) is expected


def test_dev_has_every_platform_permission() -> None:
    assert P.platform_permissions_for(P.PLATFORM_DEV) == P.PLATFORM_ALL_PERMISSIONS


def test_grants_are_internally_consistent() -> None:
    assert set(P.PLATFORM_ROLE_PERMISSIONS) == P.PLATFORM_ROLES
    for role, perms in P.PLATFORM_ROLE_PERMISSIONS.items():
        assert perms <= P.PLATFORM_ALL_PERMISSIONS, role


def test_unknown_platform_role_grants_nothing() -> None:
    assert P.platform_permissions_for("owner") == frozenset()   # a tenant role name → nothing here
    assert P.platform_has_permission("nonsense", P.PLATFORM_TICKETS_READ) is False


# ---- The plane-separation guarantee -----------------------------------------


def test_permission_namespaces_are_disjoint() -> None:
    # No string is both a tenant permission and a platform permission.
    assert P.PLATFORM_ALL_PERMISSIONS.isdisjoint(tenant.ALL_PERMISSIONS)


def test_platform_grants_never_leak_a_tenant_permission() -> None:
    for perms in P.PLATFORM_ROLE_PERMISSIONS.values():
        assert perms.isdisjoint(tenant.ALL_PERMISSIONS)


def test_tenant_grants_never_leak_a_platform_permission() -> None:
    for perms in tenant.ROLE_PERMISSIONS.values():
        assert perms.isdisjoint(P.PLATFORM_ALL_PERMISSIONS)


@pytest.mark.parametrize("platform_only_role", ["dev", "admin", "analyst"])
def test_platform_only_role_grants_nothing_on_the_tenant_plane(platform_only_role: str) -> None:
    # A platform-only role name confers no tenant permission (it isn't a tenant role).
    assert tenant.permissions_for([platform_only_role]) == frozenset()
