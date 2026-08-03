"""Resume consumer (MVP-069) — turns `approval.resolved` into a parked-run resume.

Registered on the `approval.resolved.v1` stream: it reads the org (from the CloudEvents `subject`)
and the decision, looks up the approval's parked `run_id`, and calls `resume_after_approval`. The
consumer framework dedupes redeliveries (per event id) and `resume_after_approval` is itself
idempotent (a run that is no longer parked is a no-op), so a resolved approval resumes its run
**exactly once**.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text

from core.events.consumer import consumer
from core.events.topics import stream_name
from core.runtime.executor import resume_after_approval
from core.tenancy.middleware import org_scoped_session

RESUME_STREAM = stream_name("approval.resolved.v1")


@consumer(RESUME_STREAM, "runtime-resume")
async def on_approval_resolved(envelope: dict[str, Any]) -> None:
    org_id = UUID(str(envelope["subject"]))
    data = envelope.get("data") or {}
    approval_id = data.get("approval_id")
    if not approval_id:
        return
    async with org_scoped_session(org_id) as s:
        run_id = (
            await s.execute(
                text("SELECT run_id FROM approvals WHERE id = :id"), {"id": approval_id}
            )
        ).scalar_one_or_none()
    if run_id is None:  # a manual/non-run approval — nothing parked to resume
        return
    decision = "approve" if data.get("decision") == "approved" else "reject"
    await resume_after_approval(run_id, org_id, decision=decision)
