"""Tool registry (MVP-060) — the implementations the mediation proxy dispatches to.

Only the proxy imports this; the runtime never does (enforced by the `runtime-not-tools` guard).
Read-shaped tools that already exist are wired (`catalog.search`, `pricing.compute`, `ledger.read`).
`messages.send` runs the gated send path (the proxy has already tier-checked / an approval has
resolved). Tools that don't exist yet (`calendar.book`, `crm.*`) are registered as gated stubs that
fail closed with `provider_unavailable` — disclosed, wired when built.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
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
    """Send a customer message through the gated send path (MVP-054). The proxy has already
    tier-checked (tier-1 auto, or the run resumed past an approval), so this mints the send
    authorization — an audit capability + a single-use execution token bound to this exact send,
    like the normalizer — and calls `send()`. A gate refusal returns a structured `not sent` result
    (never crashes the run / trips the breaker)."""
    from core.approvals import tokens
    from core.audit.writer import AuditEntry
    from core.audit.writer import write as audit_write
    from core.channels.whatsapp.send import SEND_ACTION, SendRefused, send

    body = str(params.get("body") or "")
    conversation_id = params.get("conversation_id") or (
        await session.execute(
            text("SELECT conversation_id FROM agent_runs WHERE id = :r"), {"r": str(ctx.run_id)}
        )
    ).scalar_one_or_none()
    if not body or not conversation_id:
        raise GrowthOperatorError(
            "config_schema_violation", "messages.send needs a body and a conversation")
    conv_id = UUID(str(conversation_id))

    # Mint the send authorization + run the send in the proxy's session (one tenant transaction).
    # A separate session would nest a second per-org advisory lock and deadlock; keeping it in the
    # passed session also lets the send gate read the capability + token it just minted.
    capability = await audit_write(
        session,
        AuditEntry(org_id=ctx.org_id, actor_type="agent", actor_id=str(ctx.instance_id),
                   action=SEND_ACTION, resource=str(conv_id), payload={"run_id": str(ctx.run_id)}),
    )
    token = await tokens.mint(
        session, org_id=ctx.org_id,
        ctx_hash=tokens.action_hash(ctx.org_id, SEND_ACTION, str(conv_id)), tier=1)
    try:
        outcome = await send(
            org_id=ctx.org_id, conversation_id=conv_id, body=body,
            audit_id=capability.id, execution_token=token, session=session,
            message_class=params.get("message_class", "transactional"),
            figure_refs=list(params.get("figure_refs", [])),
        )
    except SendRefused as exc:
        return {"sent": False, "refused": exc.code, "conversation_id": str(conv_id)}
    return {"sent": outcome.sent, "conversation_id": str(conv_id),
            "message_id": str(outcome.message_id) if outcome.message_id else None}


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
