"""Outbound WhatsApp send adapter — the single, gated exit to customers (MVP-034 + MVP-036).

Every send passes four gates before any external call and refuses (no side effect) if any
fails:

  1. audit capability  — a fresh (<10min) audit entry authorising this exact msg.send
                         → refuse ``approval_required``
  2. execution token   — a single-use, ctx-bound ed25519 token from the policy engine (MVP-066)
                         → refuse ``approval_required``
  3. suppression       — contact is not on the suppression list for this class
                         → refuse ``suppressed_contact``
  4. consent           — marketing requires positive consent (transactional is exempt)
                         → refuse ``consent_missing``

On success it records the outbound message, emits ``msg.sent.v1`` and writes the audit
outcome; on exhausted failure it marks the message failed and emits ``msg.failed.v1``. Meta
calls go through the gated ``MetaClient`` (simulated until ``whatsapp_live_enabled``), with a
``429`` Retry-After honoured and ``5xx`` retried a bounded number of times.

Fail closed: a missing capability/token, a suppression-lookup error, or unknown consent all
block the send. Ledger/figure checks (``figure_refs``) plug in here later — MVP-054.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals import tokens
from core.audit.writer import AuditEntry, verify_capability, write_outcome
from core.audit.writer import write as audit_write
from core.channels.whatsapp.credentials import load_credentials
from core.channels.whatsapp.meta_client import MetaClient, SendResult
from core.channels.whatsapp.templates import assert_template_sendable
from core.customers.consent import POSITIVE_MARKETING
from core.events.outbox import emit
from core.pricing import extract, ledger
from core.tenancy.middleware import org_scoped_session

logger = logging.getLogger("core.channels.whatsapp.send")

SEND_ACTION = "msg.send"
FIGURE_OVERRIDE_ACTION = "msg.send.figure_override"
MAX_RETRIES = 3
_MAX_BACKOFF_S = 30.0
# PILOT-1C: the canonical predicate, shared with the workflow guard and campaign audience.
# Legacy `explicit` (written by landing capture before PILOT-1C) is accepted so a real
# consenting customer is not refused by a spelling difference.
_POSITIVE_CONSENT = POSITIVE_MARKETING

MessageClass = Literal["marketing", "transactional"]
# Ledger-check enforcement mode (MVP-054): block (default, fail-closed), warn (W2), or off.
FigureCheck = Literal["block", "warn", "off"]

Sleeper = Callable[[float], Awaitable[None]]


class SendRefused(Exception):
    """A gate blocked the send. ``code`` is a canonical error code (core.common.errors)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass
class SendOutcome:
    sent: bool
    message_id: UUID | None
    provider_message_id: str | None = None
    retryable: bool = False
    #: True when this call did NOT dispatch because the idempotency key was already claimed. The
    #: caller is seeing someone else's outcome, which is exactly the point — but a lifecycle that
    #: recorded a second "sent" transition from it would double-count a single customer touch.
    already_dispatched: bool = False


def _assert_not_suppressed(scopes: set[str], message_class: MessageClass) -> None:
    if "all" in scopes:
        raise SendRefused("suppressed_contact", "contact suppressed (all)")
    if message_class == "marketing" and "marketing" in scopes:
        raise SendRefused("suppressed_contact", "contact suppressed (marketing)")


def _assert_consent(consent_status: str, message_class: MessageClass) -> None:
    if message_class == "transactional":
        return  # transactional class is exempt from marketing consent (MVP-036)
    if consent_status not in _POSITIVE_CONSENT:
        raise SendRefused("consent_missing", f"marketing requires consent (is {consent_status!r})")


async def _assert_figures_ledgered(
    session: AsyncSession, org_id: UUID, *, body: str, conversation_id: UUID,
    mode: FigureCheck, override_by: UUID | None,
) -> None:
    """Gate 5 — no unledgered rupee amount leaves. Every figure in the outbound text must match
    an unexpired ledger row exactly (`core.pricing.ledger.match`).

    - ``block`` (default): an unmatched figure raises ``unledgered_figure`` (→ 422) — unless a
      tier-3 owner override is supplied, in which case the send proceeds and the override is
      recorded on the audit chain (count only — amounts are never logged/audited).
    - ``warn``: unmatched figures are allowed but leave a redacted breadcrumb.
    - ``off``: skip (kept for a controlled rollout only)."""
    if mode == "off":
        return
    unmatched = [
        f for f in extract.extract_amounts(body)
        if not await ledger.match(session, org_id, f.minor)
    ]
    if not unmatched:
        return
    if override_by is not None:
        await audit_write(
            session,
            AuditEntry(
                org_id=org_id, actor_type="user", actor_id=str(override_by),
                action=FIGURE_OVERRIDE_ACTION, resource=str(conversation_id),
                payload={"unledgered_count": len(unmatched)},
            ),
        )
        return
    if mode == "block":
        raise SendRefused(
            "unledgered_figure", f"{len(unmatched)} amount(s) not in the ledger"
        )
    logger.warning(
        "ledger_check.warn org=%s conv=%s unledgered=%d",
        org_id, conversation_id, len(unmatched),
    )


async def _suppression_scopes(session: AsyncSession, contact_id: UUID) -> set[str]:
    """Read the contact's suppression scopes. Any lookup error fails closed (no send)."""
    try:
        rows = (
            await session.execute(
                text("SELECT scope FROM suppressions WHERE contact_id = :cid"),
                {"cid": str(contact_id)},
            )
        ).scalars().all()
    except Exception as exc:  # noqa: BLE001 - fail closed on any suppression-lookup failure
        raise SendRefused("suppressed_contact", "suppression lookup failed") from exc
    return set(rows)


def _is_retryable(result: SendResult) -> bool:
    sc = result.status_code
    return sc is None or sc == 429 or (500 <= sc < 600)


def _backoff(attempt: int) -> float:
    return min(2.0**attempt, _MAX_BACKOFF_S)


async def _send_with_retries(
    send_fn: Callable[[], Awaitable[SendResult]], sleeper: Sleeper,
    max_retries: int = MAX_RETRIES,
) -> SendResult:
    """Call ``send_fn`` (a Meta send), honouring Retry-After on 429 and retrying 5xx up to
    ``max_retries`` times. A non-retryable status (e.g. 4xx) fails immediately."""
    attempt = 0
    while True:
        result = await send_fn()
        if result.ok or not _is_retryable(result) or attempt >= max_retries:
            return result
        delay = result.retry_after_s if result.retry_after_s is not None else _backoff(attempt)
        await sleeper(delay)
        attempt += 1


@asynccontextmanager
async def _send_session(
    passed: AsyncSession | None, org_id: UUID
) -> AsyncIterator[AsyncSession]:
    """Yield the caller's session (a single caller-owned transaction — used when `send` runs inside
    the mediation proxy, so we don't nest a second per-org advisory lock and deadlock), or a fresh
    self-committing one for standalone callers (the normalizer). With a passed session the queued
    row + outcome land in one transaction the caller commits, instead of the two-phase commit."""
    if passed is not None:
        yield passed
    else:
        async with org_scoped_session(org_id) as s:
            yield s



@dataclass(frozen=True)
class _Claim:
    message_id: UUID
    already_dispatched: bool
    sent: bool = False
    provider_message_id: str | None = None


async def _claim_dispatch(
    org_id: UUID, *, conversation_id: UUID, body: str, audit_id: UUID | None,
    idempotency_key: str, recovery_attempt_id: UUID | None,
) -> _Claim:
    """Durably claim the right to dispatch this key, in its own committed transaction.

    Returns the claim when this caller won. When another caller already holds the key, returns that
    row's current outcome with `already_dispatched=True` — the caller must NOT reach the provider.

    The row is written `dispatching`, not `queued`: between this commit and the provider's answer
    the true state is genuinely unknown, and that has to be representable. `sent` is only set once
    the provider accepts.
    """
    from sqlalchemy.exc import IntegrityError

    try:
        async with org_scoped_session(org_id) as s:
            message_id = (await s.execute(
                text(
                    "INSERT INTO messages (org_id, conversation_id, direction, sender, body, "
                    " audit_id, status, idempotency_key, recovery_attempt_id) "
                    "VALUES (:org, :conv, 'outbound', 'agent', :body, :aid, 'dispatching', "
                    " :key, :rid) RETURNING id"),
                {"org": str(org_id), "conv": str(conversation_id), "body": body,
                 "aid": str(audit_id) if audit_id else None, "key": idempotency_key,
                 "rid": str(recovery_attempt_id) if recovery_attempt_id else None},
            )).scalar_one()
        return _Claim(message_id=message_id, already_dispatched=False)
    except IntegrityError:
        pass  # someone else holds the key — read their outcome instead of sending again

    async with org_scoped_session(org_id) as s:
        row = (await s.execute(
            text("SELECT id, status, provider_message_id FROM messages "
                 "WHERE org_id = :org AND idempotency_key = :key"),
            {"org": str(org_id), "key": idempotency_key})).mappings().one()
    return _Claim(
        message_id=row["id"], already_dispatched=True,
        sent=row["status"] == "sent", provider_message_id=row["provider_message_id"])


async def send(
    *,
    org_id: UUID,
    conversation_id: UUID,
    body: str,
    audit_id: UUID | None,
    execution_token: str | None,
    figure_refs: Sequence[str] = (),
    figure_check: FigureCheck = "block",
    figure_override_by: UUID | None = None,
    message_class: MessageClass = "marketing",
    template: tuple[str, str] | None = None,
    template_parameters: Sequence[str] = (),
    idempotency_key: str | None = None,
    recovery_attempt_id: UUID | None = None,
    meta_client: MetaClient | None = None,
    sleeper: Sleeper = asyncio.sleep,
    session: AsyncSession | None = None,
) -> SendOutcome:
    """Send on ``conversation_id`` after the gates. Raises ``SendRefused`` (or
    ``TemplateNotSendable``) if a gate blocks it; otherwise attempts the send and returns the
    recorded outcome.

    ``template`` = (template_key, language) sends an approved template instead of freeform
    text (``body`` is still stored as the message record); a non-approved template is refused
    by the MVP-035 gate. ``template_parameters`` fill the template's ``{{1}}``, ``{{2}}`` … body
    variables.

    ``idempotency_key`` makes dispatch **at most once** for that key within the org. The queued row
    is claimed in its OWN committed transaction *before* the provider is called — a uniqueness
    check performed afterwards would not help, because two workers could both reach Meta first. A
    second caller with the same key never dispatches; it returns the first call's outcome.

    Every rupee amount in ``body`` must match an unexpired ledger row
    (MVP-054); ``figure_check`` selects block/warn/off and ``figure_override_by`` is the
    tier-3 owner who accepts an unledgered figure (audited).
    """
    client = meta_client or MetaClient()

    # --- Gates + durable queued row, in one tenant-scoped transaction ---
    async with _send_session(session, org_id) as s:
        conv = (
            await s.execute(
                text(
                    "SELECT c.contact_id, c.channel_id, ct.phone, ct.consent_status "
                    "FROM conversations c JOIN contacts ct ON ct.id = c.contact_id "
                    "WHERE c.id = :conv"
                ),
                {"conv": str(conversation_id)},
            )
        ).mappings().first()
        if conv is None:  # RLS-scoped: unknown or another org's conversation
            raise SendRefused("approval_required", "unknown conversation")

        # Gate 1 — audit capability authorising this exact send.
        if audit_id is None or not await verify_capability(
            s, audit_id, action=SEND_ACTION, resource=str(conversation_id)
        ):
            raise SendRefused("approval_required", "missing or invalid audit capability")

        # Gate 2 — execution token: a single-use, ctx-bound token from the policy engine (MVP-066).
        try:
            await tokens.verify(
                s, execution_token, org_id=org_id,
                expected_ctx_hash=tokens.action_hash(org_id, SEND_ACTION, str(conversation_id)),
            )
        except tokens.TokenInvalid as exc:
            raise SendRefused("approval_required", f"execution token: {exc}") from exc

        # Gates 3 + 4 — suppression then consent (both fail-closed).
        _assert_not_suppressed(await _suppression_scopes(s, conv["contact_id"]), message_class)
        _assert_consent(conv["consent_status"], message_class)

        # Gate 5 — no unledgered rupee amount leaves (MVP-054).
        await _assert_figures_ledgered(
            s, org_id, body=body, conversation_id=conversation_id,
            mode=figure_check, override_by=figure_override_by,
        )

        # Template gate (MVP-035) — a non-approved template can never go out.
        if template is not None:
            await assert_template_sendable(s, org_id, template[0], template[1])

        creds = await load_credentials(s, org_id=org_id, channel_id=conv["channel_id"])
        if creds is None:
            raise SendRefused("approval_required", "channel not connected")

        to, phone_number_id, access_token = (
            conv["phone"], creds["phone_number_id"], creds["access_token"]
        )
        if idempotency_key is None:
            message_id = (
                await s.execute(
                    text(
                        "INSERT INTO messages "
                        "(org_id, conversation_id, direction, sender, body, audit_id, status) "
                        "VALUES (:org, :conv, 'outbound', 'agent', :body, :aid, 'queued') "
                        "RETURNING id"
                    ),
                    {"org": str(org_id), "conv": str(conversation_id), "body": body,
                     "aid": str(audit_id)},
                )
            ).scalar_one()

    # An idempotent send claims its key in a SEPARATE, IMMEDIATELY COMMITTED transaction, because
    # the caller's transaction (the mediation proxy's) is still open here and could roll back after
    # the provider had already accepted the message. The unique index on (org_id, idempotency_key)
    # is what serialises two concurrent callers: the loser blocks, then sees the violation and
    # reuses the winner's outcome instead of dispatching again.
    if idempotency_key is not None:
        claim = await _claim_dispatch(
            org_id, conversation_id=conversation_id, body=body, audit_id=audit_id,
            idempotency_key=idempotency_key, recovery_attempt_id=recovery_attempt_id)
        if claim.already_dispatched:
            return SendOutcome(
                sent=claim.sent, message_id=claim.message_id,
                provider_message_id=claim.provider_message_id, already_dispatched=True)
        message_id = claim.message_id

    # audit_id is guaranteed non-None past gate 1
    assert audit_id is not None

    # --- External send with bounded retries (gated-simulated) ---
    async def _do() -> SendResult:
        if template is not None:
            return await client.send_template(
                phone_number_id, access_token, to, template[0], template[1],
                parameters=tuple(template_parameters),
            )
        return await client.send_text(phone_number_id, access_token, to, body)

    result = await _send_with_retries(_do, sleeper)

    # --- Record the outcome (a second transaction when standalone; the same one when passed) ---
    async with _send_session(session, org_id) as s:
        if result.ok:
            await s.execute(
                text("UPDATE messages SET status='sent', provider_message_id=:pmid WHERE id=:id"),
                {"pmid": result.provider_message_id, "id": str(message_id)},
            )
            await emit(
                s, org_id=org_id, event_type="msg.sent.v1", source="whatsapp",
                payload={
                    "message_id": str(message_id),
                    "conversation_id": str(conversation_id),
                    "audit_id": str(audit_id),
                },
            )
            # GHOST-1a ignition: a delivered message carrying LEDGERED figures is a quote the
            # customer actually received → advance their lead to `quoted` and emit
            # `lead.stage_changed.v1`, which is what starts the silent-lead (ghost) recovery
            # playbook. Transition on delivery, never on merely computing a quote.
            if figure_refs:
                from core.customers.lifecycle import mark_quoted

                await mark_quoted(
                    s, org_id, contact_id=conv["contact_id"], message_id=message_id)
            await write_outcome(s, audit_id, "succeeded")
            return SendOutcome(
                sent=True, message_id=message_id,
                provider_message_id=result.provider_message_id,
            )

        retryable = _is_retryable(result)
        await s.execute(
            text("UPDATE messages SET status='failed' WHERE id=:id"), {"id": str(message_id)}
        )
        await emit(
            s, org_id=org_id, event_type="msg.failed.v1", source="whatsapp",
            payload={
                "message_id": str(message_id),
                "error": (result.error or f"status {result.status_code}")[:500],
                "retryable": retryable,
            },
        )
        await write_outcome(s, audit_id, "failed", detail=result.error)
        return SendOutcome(sent=False, message_id=message_id, retryable=retryable)
