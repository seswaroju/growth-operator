"""Running a classification `agent_task` (PILOT-1C).

The bridge between a workflow step that declares structured output and the non-effectful diagnosis
path. It resolves three things the platform will not guess at, and refuses if any is missing:

*Authority.* The archetype implementing a purchased capability is internal, so it needs an
internal-worker grant — matched on the run's persisted workflow key, never on anything the step
says about itself.

*The prompt.* Taken from the pack's installed prompt layer for exactly this (archetype, task). With
no layer the step **fails**; it does not fall back to a generic instruction. An unbound diagnosis is
a model improvising about a real customer, and its output is indistinguishable from a grounded one.

*The answer set.* Loaded from the installed pack. Core never knows what the reasons are.

The result is bound to workflow vars by the caller. Nothing here can send, call a tool, or touch a
customer — the worst outcome of a bad model response is an abstention.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import text

from core.runtime.diagnosis import (
    Diagnosis,
    PromptBindingMissing,
    build_system_prompt,
    build_user_prompt,
    parse_diagnosis,
)
from core.tenancy.middleware import org_scoped_session
from core.tenancy.repository import set_org_context

logger = logging.getLogger(__name__)

_VERTICALS_ROOT = Path(__file__).resolve().parents[2] / "verticals"

#: The pack file declaring the closed answer set for a classification task. Read by convention from
#: the installed pack's own directory, so `core/` names no reason and no vertical (Rule Zero).
_TAXONOMY_FILE = "agents/ghost_reasons.yaml"


def load_taxonomy(vertical: str) -> dict[str, Any]:
    path = _VERTICALS_ROOT / vertical / _TAXONOMY_FILE
    if not path.is_file():
        return {}
    parsed = yaml.safe_load(path.read_text()) or {}
    return parsed if isinstance(parsed, dict) else {}


async def _prompt_layer(org_id: UUID, archetype: str, task: str) -> str:
    """The pack's installed vertical prompt layer for this task. Raises when unbound."""
    async with org_scoped_session(org_id) as s:
        await set_org_context(s, org_id)
        content = (await s.execute(
            text("SELECT content FROM prompt_layers WHERE layer_type = 'vertical' "
                 "AND archetype = :a AND task = :t AND status = 'active' "
                 "ORDER BY version DESC LIMIT 1"),
            {"a": archetype, "t": task})).scalar_one_or_none()
    if not content:
        raise PromptBindingMissing(archetype, task)
    return str(content)


async def _vertical_for(org_id: UUID) -> str | None:
    async with org_scoped_session(org_id) as s:
        await set_org_context(s, org_id)
        return (await s.execute(
            text("SELECT vertical FROM organizations WHERE id = :o"),
            {"o": str(org_id)})).scalar_one_or_none()


async def run(
    org_id: UUID, instance_id: UUID, instr: dict[str, Any]
) -> dict[str, Any] | None:
    """Execute a classification step. Returns the bound vars, a failure, or None to defer.

    `None` means "this is not a diagnosis task" — the caller then runs the ordinary agent path, so
    adding structured output to some future step does not silently reroute it here.
    """
    from core.runtime import internal_workers

    archetype, task = str(instr["archetype"]), str(instr["task"])
    if not internal_workers.is_internal_archetype(archetype):
        return None

    vertical = await _vertical_for(org_id)
    taxonomy = load_taxonomy(str(vertical)) if vertical else {}
    if not taxonomy.get("reasons"):
        # No declared answer set means no closed set to validate against, and an open-ended
        # classification is exactly what this design refuses to put in front of an owner.
        logger.warning("diagnosis.no_taxonomy: vertical=%s task=%s", vertical, task)
        return {"status": "failed", "reason": "no_taxonomy", "task": task}

    try:
        layer = await _prompt_layer(org_id, archetype, task)
    except PromptBindingMissing:
        logger.warning("diagnosis.unbound_prompt: %s/%s", archetype, task)
        return {"status": "failed", "reason": "prompt_binding_missing", "task": task}

    activation = instr.get("_activation") or {}
    subject = activation.get("subject") or {}
    thread = subject.get("pre_silence_thread") or []
    item = subject.get("quoted_catalog_item")

    from core.common.errors import GrowthOperatorError
    from core.runtime.llm_client import ProviderCallFailed, complete

    try:
        response = await complete(
            build_system_prompt(layer, taxonomy),
            build_user_prompt(thread, item),
            max_tokens=600, timeout=30.0)
        result = parse_diagnosis(response.text, taxonomy)
    except (ProviderCallFailed, GrowthOperatorError) as exc:
        # A provider outage must not stall a store's recovery queue. Abstaining routes the lead to
        # the owner's ranked pick, which is a designed product path rather than a degraded one —
        # and the owner sees the lead either way.
        logger.warning("diagnosis.provider_failed: %s", type(exc).__name__)
        result = parse_diagnosis("", taxonomy)

    logger.info("diagnosis.complete: task=%s abstain=%s", task, result.abstain)
    return {"status": "succeeded", "task": task, **_narrow(result, instr.get("output"))}


def _narrow(result: Diagnosis, keys: list[str] | None) -> dict[str, Any]:
    """Return only the keys the step declared, so a workflow reads exactly what it asked for."""
    values = result.as_vars()
    return {k: values.get(k) for k in keys} if keys else values
