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
    from core.pricing.service import compute_quote, quote_presentation

    quote_id = await compute_quote(
        session, ctx.org_id, strategy_key=str(params["strategy"]),
        inputs=params.get("inputs", {}), params=params.get("params", {}),
    )
    # Return the deterministic two-step presentation (JWL-EST-01 phase 2) so the concierge relays
    # exact ledgered figures: `price_line` for the first reply, `breakdown_text` on request.
    return await quote_presentation(session, ctx.org_id, quote_id)


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
    from core.channels.whatsapp.templates import TemplateNotSendable

    body = str(params.get("body") or "")
    conversation_id = params.get("conversation_id") or (
        await session.execute(
            text("SELECT conversation_id FROM agent_runs WHERE id = :r"), {"r": str(ctx.run_id)}
        )
    ).scalar_one_or_none()

    # Template send (PILOT-1C). A ghost lead is silent by definition, so the 24-hour service window
    # has closed and WhatsApp accepts only an approved template. The key is a pack-authored
    # constant reaching this tool through the workflow DSL — never composed by a model — and the
    # MVP-035 gate independently refuses any key this store has not had approved, so an agent that
    # invented one gets `TemplateNotSendable` rather than a send.
    template_key = params.get("template_key")
    template: tuple[str, str] | None = None
    if template_key:
        template = (str(template_key), str(params.get("template_language") or "en"))
        # `body` is what we STORE as the conversation record; the wire content is the approved
        # template. Storing the rendered text keeps the owner's inbox honest about what was sent.
        body = body or await _render_stored_body(session, ctx.org_id, template, params)
    if not body or not conversation_id:
        raise GrowthOperatorError(
            "config_schema_violation", "messages.send needs a body and a conversation")
    conv_id = UUID(str(conversation_id))
    attempt_id = (UUID(str(params["recovery_attempt_id"]))
                  if params.get("recovery_attempt_id") else None)

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
            template=template,
            template_parameters=tuple(str(p) for p in params.get("template_parameters", ())),
            idempotency_key=(str(params["idempotency_key"])
                             if params.get("idempotency_key") else None),
            recovery_attempt_id=attempt_id,
        )
    except SendRefused as exc:
        await _record_attempt_outcome(session, ctx.org_id, attempt_id, blocked=exc.code)
        return {"sent": False, "refused": exc.code, "conversation_id": str(conv_id)}
    except TemplateNotSendable as exc:
        # Not an error the run should die on: the store's template is missing or unapproved, which
        # is an operational fact the owner can fix. Reported, never retried into a second attempt.
        await _record_attempt_outcome(
            session, ctx.org_id, attempt_id, blocked="template_not_sendable")
        return {"sent": False, "refused": "template_not_sendable", "template": exc.template_key,
                "conversation_id": str(conv_id)}

    # The lifecycle transition happens in the proxy's transaction, alongside the message row and
    # the audit outcome — so an attempt can never be marked sent by a transaction that later rolls
    # back, and can never be left `proposed` after a message actually went out.
    if attempt_id is not None and not outcome.already_dispatched:
        from core.customers import recovery_attempts

        if outcome.sent:
            await recovery_attempts.mark_sent(
                session, ctx.org_id, attempt_id, message_id=outcome.message_id,
                template_key=template[0] if template else None,
                template_language=template[1] if template else None)
        else:
            # A retryable provider failure is genuinely ambiguous: the request may have been
            # accepted before we lost the answer. Recorded as `delivery_unknown` — which counts as
            # a touch — rather than resolved into a second message to the same customer.
            await recovery_attempts.mark_failed(
                session, ctx.org_id, attempt_id,
                reason="provider_send_failed", unknown=outcome.retryable)
    return {"sent": outcome.sent, "conversation_id": str(conv_id),
            "message_id": str(outcome.message_id) if outcome.message_id else None,
            "provider_message_id": outcome.provider_message_id,
            "already_dispatched": outcome.already_dispatched}


async def _record_attempt_outcome(
    session: AsyncSession, org_id: UUID, attempt_id: UUID | None, *, blocked: str
) -> None:
    """A gate refused before any external effect. Recorded so the owner can see *why* a silent lead
    was not contacted — an invisible refusal is indistinguishable from a broken product."""
    if attempt_id is None:
        return
    from core.customers import recovery_attempts

    await recovery_attempts.mark_blocked(session, org_id, attempt_id, reason=blocked)


async def _render_stored_body(
    session: AsyncSession, org_id: UUID, template: tuple[str, str], params: dict[str, Any]
) -> str:
    """The text stored on the conversation for a template send: the approved template body with its
    `{{n}}` placeholders filled from the same parameters sent to Meta. Read from the store's own
    approved template row — never reconstructed by a model, so the record cannot drift from the
    wire content."""
    from core.channels.whatsapp.templates import get_template

    tpl = await get_template(session, org_id, template[0], template[1])
    text_body = str((tpl or {}).get("body") or "")
    for i, value in enumerate(params.get("template_parameters", ()), start=1):
        text_body = text_body.replace(f"{{{{{i}}}}}", str(value))
    return text_body or template[0]


async def _landing_generate(
    ctx: RunContext, params: dict[str, Any], session: AsyncSession, audit_id: UUID
) -> Any:
    """LP-2d: the marketing agent drafts N candidate landing pages (internal drafts, no approval to
    run — status 'generated'). The owner reviews + selects one (LP-2b); publishing is separate +
    approval-gated."""
    from core.landing import service
    from core.landing.plan import CampaignContext, ProductRef

    slug = str(params.get("slug") or "")
    if not slug:
        raise GrowthOperatorError("config_schema_violation", "landing_page.generate needs a slug")
    products = [
        ProductRef(str(p.get("title", "")), str(p.get("price_text", "")), p.get("image_url"))
        for p in params.get("products", []) if isinstance(p, dict)]
    campaign = CampaignContext(
        headline=str(params.get("headline", "")), offer=str(params.get("offer", "")),
        subheadline=str(params.get("subheadline", "")),
        objective=str(params.get("objective", "whatsapp")),
        hero_image_url=params.get("hero_image_url"), products=products,
        wa_number=str(params.get("wa_number", "")))
    page_id, rows = await service.generate_variants(
        session, ctx.org_id, campaign=campaign, slug=slug,
        n=int(params.get("variants", 3)), use_llm=bool(params.get("use_llm", False)))
    return {"page_id": str(page_id), "variants": rows}


async def _landing_publish(
    ctx: RunContext, params: dict[str, Any], session: AsyncSession, audit_id: UUID
) -> Any:
    """LP-2d: publish an owner-approved landing page. This runs only AFTER the mediation tier gate
    (the publish parks for owner approval); it audits the transition as an `agent` action."""
    from core.audit.taxonomy import ACTOR_AGENT
    from core.landing import lifecycle

    page_id = UUID(str(params["page_id"]))
    status = await lifecycle.publish(
        session, ctx.org_id, page_id, actor_id=ctx.instance_id, actor_type=ACTOR_AGENT)
    if status is None:
        raise GrowthOperatorError("provider_unavailable", "landing page not found")
    return {"status": status}


def _not_wired(name: str) -> ToolImpl:
    async def _impl(
        ctx: RunContext, params: dict[str, Any], session: AsyncSession, audit_id: UUID
    ) -> Any:
        raise GrowthOperatorError("provider_unavailable", f"{name} not built yet")

    return _impl


#: Commercial classification for every tool (PLAN-5). A tool either names the capability that must
#: be in the store's current plan, or records why it needs none. The CI guard refuses a REGISTRY
#: entry missing from this map, so a new tool cannot quietly become an ungated execution path —
#: "hidden tool" has never meant "inaccessible capability".
TOOL_CAPABILITY: dict[str, str] = {
    "catalog.search": "catalog",
    # quoting is catalog-grounded; `pricing` itself is RBAC-governed, never a second gate
    "pricing.compute": "catalog",
    "messages.send": "conversations",
    "landing_page.generate": "landing_pages",
    "landing_page.publish": "landing_pages",
}

#: Tools that legitimately need no plan grant, with the reason recorded for review.
TOOL_PLAN_EXEMPT: dict[str, str] = {
    "ledger.read": "reads the quote ledger backing a figure already shown — diagnostic, not paid "
                   "computation, and needed to explain past activity after cancellation",
    "calendar.book": "not wired — raises provider_unavailable before any effect",
    "crm.read": "not wired — raises provider_unavailable before any effect",
    "crm.write": "not wired — raises provider_unavailable before any effect",
}


REGISTRY: dict[str, ToolImpl] = {
    "catalog.search": _catalog_search,
    "pricing.compute": _pricing_compute,
    "ledger.read": _ledger_read,
    "messages.send": _messages_send,
    "landing_page.generate": _landing_generate,
    "landing_page.publish": _landing_publish,
    "calendar.book": _not_wired("calendar.book"),
    "crm.read": _not_wired("crm.read"),
    "crm.write": _not_wired("crm.write"),
}
