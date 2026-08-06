"""Support tickets (support-tickets track, DECISIONS 2026-08-05).

A store owner raises an issue from their console (`POST /v1/support/tickets`, org-scoped); it lands
in the Growth Operator operator queue (`/v1/admin/support/tickets`, cross-tenant via the audited
platform-admin path in `core.tenancy.platform_admin`) with priority + severity; the operator
resolves it. Platform-invariant (L0) — no vertical/industry nouns.
"""
