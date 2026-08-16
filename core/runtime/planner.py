"""Planner routing (MVP-056).

The single consumer on `msg.received.v1`: it classifies the inbound message to an intent, resolves
that intent to an archetype+task via the pack taxonomy, checks three global guards, and enqueues an
agent run against the org's active instance for that archetype. An unclassified message falls back
to the concierge to clarify.

Classification is **gated-simulated** (like the LLM/embedder/providers): a deterministic keyword
matcher over the pack's `intent_keywords`, replaceable by a small classifier model at go-live.
Nothing here fabricates a customer reply — it only decides *which* agent handles the turn; the run
then goes through the same mediation/approval/audit spine as everything else.

Guards (fail-safe, checked in order): **tenant paused** (org not active → drop), **suppression**
(contact opted out of the routed class → drop), **frequency cap** (per the pack's
`contact_frequency_cap`; inbound replies are transactional and exempt, so the cap bounds
planner-initiated *marketing* touches).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events.consumer import consumer
from core.events.redis_client import event_redis
from core.packs.taxonomy import Route, Taxonomy, load_taxonomy
from core.runtime.executor import start_run
from core.tenancy.middleware import org_scoped_session

logger = logging.getLogger("core.runtime.planner")

MSG_RECEIVED_STREAM = "gop:events:msg.received.v1"
_MARKETING_ARCHETYPES = frozenset({"nurture", "campaigner"})
_taxonomy_cache: dict[str, Taxonomy] = {}


# ---- classification + routing (pure) ---------------------------------------------------

def classify(body: str, keywords: dict[str, list[str]]) -> str | None:
    """Return the intent whose longest keyword appears in `body` (case-insensitive), else None.
    Longest-match-wins makes a specific phrase ('exchange policy') beat a generic one ('exchange');
    ties break by intent name for determinism."""
    text_l = body.lower()
    best: tuple[int, str] | None = None  # (keyword length, intent) — max wins
    for intent, kws in keywords.items():
        for kw in kws:
            if kw and kw in text_l:
                cand = (len(kw), intent)
                if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1] < best[1]):
                    best = cand
    return best[1] if best else None


def route_message(body: str, taxonomy: Taxonomy) -> tuple[Route, str | None, bool]:
    """Classify + resolve. Returns (route, intent, clarify). An unknown intent falls back to the
    concierge with `clarify=True` (routing_golden covers this end-to-end)."""
    intent = classify(body, taxonomy.intent_keywords)
    route = taxonomy.intent_routes.get(intent) if intent is not None else None
    if route is None:
        return taxonomy.fallback, intent, True
    return route, intent, False


def archetype_class(archetype: str) -> str:
    """Message class for the guards: marketing archetypes send marketing; everything else replies
    in-conversation (transactional)."""
    return "marketing" if archetype in _MARKETING_ARCHETYPES else "transactional"


# ---- guards (fail-safe) ----------------------------------------------------------------

def is_tenant_paused(status: str | None) -> bool:
    """A tenant that is not `active` is paused — the planner drops its traffic."""
    return status != "active"


def suppression_blocks(scopes: set[str], message_class: str) -> bool:
    """Mirror the send-path suppression rule: a fully opted-out contact (`all`) is always blocked;
    a `marketing` suppression blocks only marketing-class routing (an inbound reply still flows)."""
    if "all" in scopes:
        return True
    return message_class == "marketing" and "marketing" in scopes


def _cap_key(org_id: UUID, contact_id: UUID, day: str) -> str:
    return f"gop:planner:mkt:{org_id}:{contact_id}:{day}"


async def frequency_cap_blocks(
    redis: Redis, org_id: UUID, contact_id: UUID, message_class: str, cap: dict[str, Any],
    *, now: datetime | None = None,
) -> bool:
    """True if this touch exceeds the contact's daily cap. The exempt classes (transactional /
    active-conversation replies) never count — so the cap bounds marketing touches only."""
    exempt = set(cap.get("exempt", []))
    if message_class in exempt:
        return False
    max_per_day = int(cap.get("max_msgs_per_contact_per_day", 0) or 0)
    if max_per_day <= 0:
        return False
    day = (now or datetime.now(UTC)).strftime("%Y%m%d")
    used = int(await redis.get(_cap_key(org_id, contact_id, day)) or 0)
    return used >= max_per_day


async def record_marketing_touch(
    redis: Redis, org_id: UUID, contact_id: UUID, *, now: datetime | None = None
) -> None:
    """Count a marketing touch against the daily cap (the send path calls this; the planner reads
    it). A 2-day TTL keeps the counter self-cleaning."""
    day = (now or datetime.now(UTC)).strftime("%Y%m%d")
    key = _cap_key(org_id, contact_id, day)
    await redis.incr(key)
    await redis.expire(key, 172800)


# ---- taxonomy + DB helpers -------------------------------------------------------------

def get_taxonomy(slug: str, *, root: Any = None) -> Taxonomy:
    if root is not None:  # test override — never cache a custom root
        return load_taxonomy(slug, root=root)
    if slug not in _taxonomy_cache:
        _taxonomy_cache[slug] = load_taxonomy(slug)
    return _taxonomy_cache[slug]


async def _org_status_and_pack(
    session: AsyncSession, org_id: UUID
) -> tuple[str | None, str | None]:
    row = (
        await session.execute(
            text(
                "SELECT o.status AS status, "
                " (SELECT p.slug FROM agent_instances i "
                "  JOIN agent_bindings b ON b.id = i.binding_id "
                "  JOIN packs p ON p.id = b.pack_id "
                "  WHERE i.org_id = o.id LIMIT 1) AS slug "
                "FROM organizations o WHERE o.id = :org"
            ),
            {"org": str(org_id)},
        )
    ).mappings().first()
    return (row["status"], row["slug"]) if row else (None, None)


async def _suppression_scopes(session: AsyncSession, contact_id: UUID) -> set[str]:
    rows = await session.execute(
        text("SELECT scope FROM suppressions WHERE contact_id = :c"), {"c": str(contact_id)}
    )
    return {r[0] for r in rows}


async def _active_instance(session: AsyncSession, org_id: UUID, archetype: str) -> UUID | None:
    return (
        await session.execute(
            text(
                "SELECT i.id FROM agent_instances i "
                "JOIN agent_bindings b ON b.id = i.binding_id "
                "JOIN agent_archetypes a ON a.id = b.archetype_id "
                "WHERE i.org_id = :org AND a.slug = :arch AND i.status = 'active' LIMIT 1"
            ),
            {"org": str(org_id), "arch": archetype},
        )
    ).scalar_one_or_none()


# ---- consumer --------------------------------------------------------------------------

async def _handle(
    envelope: dict[str, Any], *, redis: Redis | None = None, start_run_fn: Any = None,
    taxonomy_root: Any = None,
) -> str:
    """Route one inbound message. Returns a short outcome string (for logs/tests): `enqueued`,
    `paused`, `suppressed`, `capped`, `no_pack`, or `no_instance`."""
    start_run_fn = start_run_fn or start_run
    data = envelope.get("data") or {}
    org_id = UUID(str(envelope["subject"]))
    conversation_id = data.get("conversation_id")
    contact_id = data.get("contact_id")
    body = str(data.get("body") or "")
    own_redis = redis is None
    redis = redis or event_redis()
    try:
        async with org_scoped_session(org_id) as s:
            status, slug = await _org_status_and_pack(s, org_id)
        if is_tenant_paused(status):
            logger.info("planner: tenant paused, dropping org=%s", org_id)
            return "paused"
        if slug is None:
            logger.warning("planner: no installed pack for org=%s", org_id)
            return "no_pack"

        taxonomy = get_taxonomy(slug, root=taxonomy_root)
        route, intent, clarify = route_message(body, taxonomy)
        msg_class = archetype_class(route.archetype)

        async with org_scoped_session(org_id) as s:
            scopes = await _suppression_scopes(s, UUID(str(contact_id))) if contact_id else set()
        if suppression_blocks(scopes, msg_class):
            logger.info("planner: contact suppressed (%s), dropping", msg_class)
            return "suppressed"

        if await frequency_cap_blocks(
            redis, org_id, UUID(str(contact_id)) if contact_id else org_id, msg_class,
            taxonomy.frequency_cap,
        ):
            logger.info("planner: frequency cap hit, dropping")
            return "capped"

        async with org_scoped_session(org_id) as s:
            instance_id = await _active_instance(s, org_id, route.archetype)
        if instance_id is None:
            logger.warning("planner: no active %s instance for org=%s", route.archetype, org_id)
            return "no_instance"

        await start_run_fn(
            org_id, instance_id, trigger="msg.received",
            input={"body": body, "intent": intent, "task": route.task, "clarify": clarify},
            conversation_id=UUID(str(conversation_id)) if conversation_id else None,
        )
        logger.info("planner: routed intent=%s -> %s/%s", intent, route.archetype, route.task)
        return "enqueued"
    finally:
        if own_redis:
            await redis.aclose()


@consumer(MSG_RECEIVED_STREAM, "planner")
async def on_msg_received(envelope: dict[str, Any]) -> None:
    await _handle(envelope)
