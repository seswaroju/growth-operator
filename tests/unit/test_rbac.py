"""Tenant RBAC matrix + @requires enforcement + role grant-hierarchy (Phase 1.1).

No database needed: enforcement is constant-based. The HTTP checks mount a tiny app with a
`requires(...)`-guarded route and assert the 403 problem+json names the missing permission.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import Depends, FastAPI

from core.common.config import get_settings
from core.tenancy import auth, permissions
from core.tenancy.deps import CurrentAuth
from core.tenancy.permissions import (
    ALL_ROLES,
    APPROVALS_RESOLVE,
    BILLING_MANAGE,
    CAMPAIGNS_SEND,
    CATALOG_READ,
    CATALOG_WRITE,
    CONVERSATIONS_RESPOND,
    MEMBERS_INVITE,
    MEMBERS_MANAGE,
    ORG_MANAGE,
    ROLE_MANAGER,
    ROLE_OWNER,
    ROLE_STAFF,
    ROLE_VIEWER,
    assignable_roles,
    can_grant_role,
    has_permission,
)
from core.tenancy.rbac import register_rbac_handlers, requires

# role × sample-permission matrix.
MATRIX = [
    (ROLE_OWNER, APPROVALS_RESOLVE, True),
    (ROLE_OWNER, ORG_MANAGE, True),
    (ROLE_OWNER, BILLING_MANAGE, True),
    (ROLE_MANAGER, CATALOG_WRITE, True),
    (ROLE_MANAGER, APPROVALS_RESOLVE, True),
    (ROLE_MANAGER, MEMBERS_INVITE, True),
    (ROLE_MANAGER, ORG_MANAGE, False),      # manager: no settings
    (ROLE_MANAGER, MEMBERS_MANAGE, False),  # manager: no member-management
    (ROLE_STAFF, APPROVALS_RESOLVE, True),      # staff is operational now
    (ROLE_STAFF, CONVERSATIONS_RESPOND, True),
    (ROLE_STAFF, CATALOG_WRITE, False),
    (ROLE_STAFF, CAMPAIGNS_SEND, False),
    (ROLE_VIEWER, CATALOG_READ, True),
    (ROLE_VIEWER, APPROVALS_RESOLVE, False),     # viewer is read-only
    (ROLE_VIEWER, CONVERSATIONS_RESPOND, False),
]


@pytest.mark.parametrize(("role", "perm", "expected"), MATRIX)
def test_permission_matrix(role: str, perm: str, expected: bool) -> None:
    assert has_permission([role], perm) is expected


def test_no_role_denies_everything() -> None:
    assert has_permission([], APPROVALS_RESOLVE) is False
    assert has_permission(["nonsense"], CATALOG_READ) is False
    assert has_permission(["founder"], ORG_MANAGE) is False  # retired role grants nothing


def test_constants_are_internally_consistent() -> None:
    for role, perms in permissions.ROLE_PERMISSIONS.items():
        assert perms <= permissions.ALL_PERMISSIONS, role
    assert permissions.ROLE_PERMISSIONS[ROLE_OWNER] == permissions.ALL_PERMISSIONS  # owner = all
    assert set(permissions.ROLE_PERMISSIONS) == ALL_ROLES  # exactly the four roles


# ---- Grant hierarchy (you can't grant a role above your own) ----------------


@pytest.mark.parametrize(("actor", "target", "ok"), [
    (ROLE_OWNER, ROLE_OWNER, True), (ROLE_OWNER, ROLE_VIEWER, True),
    (ROLE_MANAGER, ROLE_STAFF, True), (ROLE_MANAGER, ROLE_MANAGER, True),
    (ROLE_MANAGER, ROLE_OWNER, False),   # can't grant above yourself
    (ROLE_STAFF, ROLE_VIEWER, True), (ROLE_STAFF, ROLE_MANAGER, False),
    (ROLE_VIEWER, ROLE_STAFF, False),
])
def test_can_grant_role_respects_rank(actor: str, target: str, ok: bool) -> None:
    assert can_grant_role([actor], target) is ok


def test_can_grant_rejects_unknown_role() -> None:
    assert can_grant_role([ROLE_OWNER], "founder") is False


def test_assignable_roles_by_rank() -> None:
    assert assignable_roles([ROLE_OWNER]) == ALL_ROLES
    assert assignable_roles([ROLE_MANAGER]) == {ROLE_MANAGER, ROLE_STAFF, ROLE_VIEWER}
    assert assignable_roles([ROLE_VIEWER]) == {ROLE_VIEWER}
    assert assignable_roles([]) == frozenset()


# ---- HTTP enforcement -------------------------------------------------------


def _token(roles: list[str]) -> str:
    return auth.issue_access_token(
        sub=str(uuid.uuid4()), secret=get_settings().jwt_secret,
        org_id=str(uuid.uuid4()), roles=roles,
    )


def _guarded_app() -> FastAPI:
    app = FastAPI()
    register_rbac_handlers(app)

    @app.get("/manage")
    def manage(current: CurrentAuth = Depends(requires(ORG_MANAGE))) -> dict:
        return {"ok": True, "user": str(current.user_id)}

    return app


async def _get(app: FastAPI, roles: list[str] | None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {_token(roles)}"} if roles is not None else {}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/manage", headers=headers)


async def test_owner_allowed_to_manage() -> None:
    r = await _get(_guarded_app(), [ROLE_OWNER])
    assert r.status_code == 200 and r.json()["ok"] is True


async def test_manager_denied_with_problem_json_naming_permission() -> None:
    r = await _get(_guarded_app(), [ROLE_MANAGER])  # manager lacks org:manage
    assert r.status_code == 403
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["permission"] == ORG_MANAGE and ORG_MANAGE in body["detail"]
    assert body["status"] == 403


async def test_missing_token_is_401_not_403() -> None:
    r = await _get(_guarded_app(), None)
    assert r.status_code == 401
