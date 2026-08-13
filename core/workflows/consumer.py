"""Workflow wake consumers (MVP-073b).

A separate consumer group on `msg.received.v1` (independent of the planner's group): every inbound
message wakes any workflow reply-wait on that conversation. Idempotent — `match_reply` claims a
subscription atomically, so a redelivered message wakes the run at most once.

Event-wait fan-in (a generic consumer that also drives `triggers.match_and_start` and
`waits.match_event`) is wired when a workflow needs a live event trigger; the matching logic lives
and tested here and in `triggers`. Until then, event-waits resolve via the timeout sweep.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text

from core.events.consumer import consumer
from core.events.topics import stream_name
from core.tenancy.middleware import org_scoped_session
from core.workflows import executor, waits

logger = logging.getLogger(__name__)


@consumer(stream_name("msg.received.v1"), "workflow-reply-wait")
async def on_msg_received(envelope: dict[str, Any]) -> None:
    """Inbound message → wake reply-waits on its conversation."""
    org_id = UUID(str(envelope["subject"]))
    data = envelope.get("data") or {}
    conversation_id = data.get("conversation_id")
    if not conversation_id:
        return
    await waits.match_reply(org_id, UUID(str(conversation_id)))


@consumer(stream_name("lead.went_silent.v1"), "workflow-silent-lead")
async def on_lead_went_silent(envelope: dict[str, Any]) -> None:
    """A silent lead → start the recovery playbook, grounded in facts read here.

    A **static** consumer, deliberately. The alternative — a generic consumer subscribing to every
    event type some tenant's workflow happens to name — would let a stored trigger definition
    decide which streams the platform consumes, and the set of things a workflow can react to is a
    platform decision, not tenant configuration.

    The event carries identifiers; the conversation, the pre-silence thread and any provable quoted
    item are loaded under tenant scope (see `core.customers.recovery_context`). If they cannot be
    assembled the run does not start: a recovery that cannot identify its own conversation would
    send into the dark and be unable to recognise the reply.
    """
    from core.customers.recovery_context import RecoveryContextUnavailable, build
    from core.tenancy.repository import set_org_context
    from core.workflows import triggers

    org_id = UUID(str(envelope["subject"]))
    payload = dict(envelope.get("data") or {})
    if not payload.get("lead_id"):
        return
    async with org_scoped_session(org_id) as s:
        await set_org_context(s, org_id)
        try:
            ctx = await build(s, org_id, payload)
        except RecoveryContextUnavailable as exc:
            logger.info("recovery.not_started: lead %s (%s)", payload.get("lead_id"), exc.reason)
            return
    await triggers.match_and_start(
        org_id, "lead.went_silent.v1", {**payload, **ctx.as_subject()})


@consumer(stream_name("approval.resolved.v1"), "workflow-human-task")
async def on_approval_resolved(envelope: dict[str, Any]) -> None:
    """A resolved `workflow.human_task` approval → resume the parked run (approve advances; reject
    compensates). Other approval kinds are left to their own consumer groups on this stream."""
    org_id = UUID(str(envelope["subject"]))
    data = envelope.get("data") or {}
    approval_id = data.get("approval_id")
    if not approval_id:
        return
    async with org_scoped_session(org_id) as s:
        appr = (await s.execute(
            text("SELECT action_type, payload FROM approvals WHERE id = :id"),
            {"id": str(approval_id)})).mappings().first()
    if appr is None or appr["action_type"] != executor.WORKFLOW_HUMAN_ACTION:
        return
    payload = appr["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    wf_run_id = payload.get("workflow_run_id")
    if not wf_run_id:
        return
    decision = "approved" if data.get("decision") == "approved" else "rejected"
    await executor.resume_human(org_id, UUID(str(wf_run_id)), decision)


@consumer(stream_name("approval.resolved.v1"), "workflow-tool-call")
async def on_tool_approval_resolved(envelope: dict[str, Any]) -> None:
    """A resolved `workflow.tool_call` approval → re-enter the parked step (approve) or compensate
    (reject). Its own consumer group, so a failure resuming a tool cannot stall human-task wakes."""
    org_id = UUID(str(envelope["subject"]))
    data = envelope.get("data") or {}
    approval_id = data.get("approval_id")
    if not approval_id:
        return
    async with org_scoped_session(org_id) as s:
        appr = (await s.execute(
            text("SELECT action_type, payload FROM approvals WHERE id = :id"),
            {"id": str(approval_id)})).mappings().first()
    if appr is None or appr["action_type"] != executor.WORKFLOW_TOOL_ACTION:
        return
    payload = appr["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    wf_run_id = payload.get("workflow_run_id")
    if not wf_run_id:
        return
    decision = "approved" if data.get("decision") == "approved" else "rejected"
    # The approved tool comes from the APPROVAL's payload, not the event — the human approved a
    # specific effect, and the event only reports their verdict.
    await executor.resume_tool(org_id, UUID(str(wf_run_id)), decision, payload.get("tool"))


@consumer(stream_name("approval.resolved.v1"), "workflow-activation")
async def on_activation_resolved(envelope: dict[str, Any]) -> None:
    """A resolved `workflow.activate` approval → activate the owner-built draft (approve) or
    leave it a draft (reject). Other approval kinds go to their own consumer groups."""
    from core.workflows import activation

    org_id = UUID(str(envelope["subject"]))
    data = envelope.get("data") or {}
    approval_id = data.get("approval_id")
    if not approval_id:
        return
    async with org_scoped_session(org_id) as s:
        appr = (await s.execute(
            text("SELECT action_type, payload FROM approvals WHERE id = :id"),
            {"id": str(approval_id)})).mappings().first()
        if appr is None or appr["action_type"] != activation.ACTIVATE_ACTION:
            return
        payload = appr["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        definition_id = payload.get("definition_id")
        if not definition_id:
            return
        await activation.apply_activation_decision(
            s, org_id, UUID(str(definition_id)), data.get("decision") == "approved")
        await s.commit()
