"""Support-ticket API contract (support-tickets track) — routes + response shapes, no DB.

Locks the owner/operator split into the contract: the four routes exist under the right prefixes,
and the owner response model never carries cross-tenant fields (org id/name, who raised it) while
the operator model does.
"""

from __future__ import annotations

from core.support.schemas import AdminTicketOut, TicketOut

_CROSS_TENANT_FIELDS = {"org_id", "org_name", "raised_by"}


def test_owner_view_hides_cross_tenant_fields() -> None:
    owner_fields = set(TicketOut.model_fields)
    leaked = owner_fields & _CROSS_TENANT_FIELDS
    assert not leaked, f"owner ticket view must not leak tenant fields: {leaked}"


def test_operator_view_includes_tenant_fields() -> None:
    admin_fields = set(AdminTicketOut.model_fields)
    assert _CROSS_TENANT_FIELDS <= admin_fields
    assert set(TicketOut.model_fields) <= admin_fields  # operator view is a superset of owner view


def test_support_routes_registered_under_expected_prefixes() -> None:
    from core.api.main import app

    paths = app.openapi()["paths"]
    assert "POST" in {m.upper() for m in paths["/v1/support/tickets"]}
    assert "GET" in {m.upper() for m in paths["/v1/support/tickets"]}
    assert "GET" in {m.upper() for m in paths["/v1/support/tickets/{ticket_id}"]}
    assert "GET" in {m.upper() for m in paths["/v1/admin/support/tickets"]}
    assert "PATCH" in {m.upper() for m in paths["/v1/admin/support/tickets/{ticket_id}"]}
