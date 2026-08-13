"""Shared test bootstrap.

Ensures the non-superuser `app_rw` role exists before any test runs, so the app engine
(which now connects as `app_rw` — MVP-016) can log in and RLS is actually enforced. Runs
`infra/db/roles.sql` via the migrator (owner) connection; a no-op when the DB is
unreachable (those tests skip themselves).
"""

from __future__ import annotations

import asyncio
import pathlib
from collections.abc import Iterator

import asyncpg
import pytest

from core.common.config import get_settings

_ROLES_SQL = pathlib.Path(__file__).resolve().parents[1] / "infra" / "db" / "roles.sql"


@pytest.fixture(scope="session", autouse=True)
def ensure_app_rw_role() -> None:
    async def _run() -> None:
        dsn = get_settings().database_migrator_url.replace("+asyncpg", "")
        try:
            conn = await asyncpg.connect(dsn, timeout=3)
        except Exception:
            return  # DB not up — DB-backed tests skip; nothing to bootstrap
        try:
            await conn.execute(_ROLES_SQL.read_text())
        finally:
            await conn.close()

    asyncio.run(_run())


# ---- PLAN-5 test support ------------------------------------------------------------------------

#: Every capability a fully-subscribed store holds. PLAN-5 gates paid execution on the plan, so a
#: fixture that exercises paid work must entitle its org — the same thing a real store does by
#: having a subscription. Fixtures that deliberately test *denial* narrow this list.
ALL_CAPABILITIES = [
    "conversations", "catalog", "customers", "ghost_recovery",
    "campaigns.whatsapp", "campaigns.analytics", "landing_pages", "catalog.ingestion",
    "jewelry.rate_operations",
]


async def entitle_org(
    conn: object, org_id: object, *, capabilities: list[str] | None = None,
    agents: list[str] | None = None, name: str | None = None,
) -> object:
    """Give `org_id` an active structured plan. Returns the plan id.

    `conn` is an asyncpg connection. Written as a helper rather than a fixture because the suites
    that need it build their worlds in very different shapes.
    """
    import json
    import uuid as _uuid

    plan_id = _uuid.uuid4()
    config = {
        "entitlement_schema_version": 1,
        "entitlements": ALL_CAPABILITIES if capabilities is None else capabilities,
        "agents": ["concierge"] if agents is None else agents,
        "channels": ["whatsapp"], "addons": [], "promotions": [], "vertical": None,
    }
    await conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO billing_plans (id, name, price_minor, features, config, max_managers, "
        "max_staff) VALUES ($1,$2,1,'[]'::jsonb,$3::jsonb,5,20)",
        plan_id, name or f"ent-{plan_id.hex[:10]}", json.dumps(config))
    await conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO billing_subscriptions (org_id, plan_id, status) VALUES ($1,$2,'active') "
        "ON CONFLICT DO NOTHING",
        org_id, plan_id)

    # A subscribed store always has its vertical pack installed, and PLAN-2 only reports an agent as
    # entitled when the tenant actually has a binding for it — so the pack install is part of what
    # "entitled" means, not an optional extra.
    for pack_id in [r["pack_id"] for r in await conn.fetch(  # type: ignore[attr-defined]
            "SELECT DISTINCT ab.pack_id FROM agent_instances ai "
            "JOIN agent_bindings ab ON ab.id = ai.binding_id WHERE ai.org_id = $1", org_id)]:
        await conn.execute(  # type: ignore[attr-defined]
            "INSERT INTO pack_installations (org_id, pack_id, status) VALUES ($1,$2,'active') "
            "ON CONFLICT (org_id, pack_id) DO UPDATE SET status = 'active'",
            org_id, pack_id)
    return plan_id


@pytest.fixture(autouse=True, scope="session")
def _classify_fixture_tools() -> Iterator[None]:
    """Fixture tools must declare a commercial classification, exactly as production tools do.

    PLAN-5 fails closed on an unclassified tool, which is the point — but several suites inject
    fictional tools to exercise the proxy chain. They are registered here under the `test.`
    namespace (never used by a real tool) and removed afterwards, so the shipped map stays honest.
    A new fixture tool fails loudly until it is added, which is the intended behaviour.
    """
    from core.mediation.tools import TOOL_PLAN_EXEMPT

    added = {
        "test.action": "test-only fixture tool (approval chain)",
        "test.flaky": "test-only fixture tool (failure/breaker chain)",
    }
    for name, reason in added.items():
        TOOL_PLAN_EXEMPT.setdefault(name, reason)
    yield
    for name in added:
        TOOL_PLAN_EXEMPT.pop(name, None)
