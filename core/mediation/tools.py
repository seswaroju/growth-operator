"""Tool registry (MVP-060) — the implementations the mediation proxy dispatches to.

Only the proxy imports this; the runtime never does (enforced by the `runtime-not-tools` guard).
Read-shaped tools that already exist are wired (`catalog.search`, `pricing.compute`, `ledger.read`).
`messages.send` is registered but reached only after a tier-2 approval (the proxy checkpoints it),
so it never fires unapproved. Tools that don't exist yet (`calendar.book`, `crm.*`) are registered
as gated stubs that fail closed with `provider_unavailable` — disclosed, wired when built.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.common.errors import GrowthOperatorError
from core.mediation.proxy import RunContext, ToolImpl


async def _catalog_search(
    ctx: RunContext, params: dict[str, Any], session: AsyncSession, audit_id: UUID
) -> Any:
    from core.catalog.search import hybrid_search

    results, nearest = await hybrid_search(
        session, ctx.org_id, str(params.get("query", "")),
        k=int(params.get("limit", 8)), filters=params.get("filters"),
    )
    return {"results": results, "nearest": nearest}


async def _pricing_compute(
    ctx: RunContext, params: dict[str, Any], session: AsyncSession, audit_id: UUID
) -> Any:
    from core.pricing.service import compute_quote

    quote_id = await compute_quote(
        session, ctx.org_id, strategy_key=str(params["strategy"]),
        inputs=params.get("inputs", {}), params=params.get("params", {}),
    )
    return {"quote_id": str(quote_id)}


async def _ledger_read(
    ctx: RunContext, params: dict[str, Any], session: AsyncSession, audit_id: UUID
) -> Any:
    from core.pricing.ledger import match

    matched = await match(session, ctx.org_id, int(params["amount_minor"]))
    return {"matched": matched}


async def _messages_send(
    ctx: RunContext, params: dict[str, Any], session: AsyncSession, audit_id: UUID
) -> Any:
    # Reached only past a tier-2 approval (the proxy checkpoints it before here). The real send
    # path (MVP-054 gates) is wired when approvals execute the queued action (MVP-065/066).
    raise GrowthOperatorError("approval_required", "messages.send executes via the approval flow")


def _not_wired(name: str) -> ToolImpl:
    async def _impl(
        ctx: RunContext, params: dict[str, Any], session: AsyncSession, audit_id: UUID
    ) -> Any:
        raise GrowthOperatorError("provider_unavailable", f"{name} not built yet")

    return _impl


REGISTRY: dict[str, ToolImpl] = {
    "catalog.search": _catalog_search,
    "pricing.compute": _pricing_compute,
    "ledger.read": _ledger_read,
    "messages.send": _messages_send,
    "calendar.book": _not_wired("calendar.book"),
    "crm.read": _not_wired("crm.read"),
    "crm.write": _not_wired("crm.write"),
}
