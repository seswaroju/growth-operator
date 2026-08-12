"""LP-2d — the landing tools at the mediation boundary (pure, no DB).

Asserts the safety wiring: the campaigner may draft pages autonomously (`landing_page.generate`
skips the tier gate) but publishing is gated (`landing_page.publish` `requires_tier_eval`), and the
level-1 allowlist still intersects the pack grant."""

from __future__ import annotations

import uuid

from core.approvals.engine import resolve_actions
from core.mediation.manifest import compile_manifest
from core.packs.archetypes import ARCHETYPE_ALLOWLISTS


def _grant(name: str) -> dict[str, str]:
    return {"name": name}


def _campaigner_manifest(tool_grants: list[dict[str, str]]) -> dict:
    return compile_manifest(
        instance_id=uuid.uuid4(), org_id=uuid.uuid4(),
        allowlist=ARCHETYPE_ALLOWLISTS["campaigner"], tool_grants=tool_grants)


def test_campaigner_allowlist_carries_the_landing_tools() -> None:
    assert "landing_page.generate" in ARCHETYPE_ALLOWLISTS["campaigner"]
    assert "landing_page.publish" in ARCHETYPE_ALLOWLISTS["campaigner"]


def test_generate_skips_tier_gate_publish_is_gated() -> None:
    m = _campaigner_manifest([_grant("landing_page.generate"), _grant("landing_page.publish")])
    tools = {t["name"]: t for t in m["tools"]}
    # generate: drafts only → no approval to run
    assert tools["landing_page.generate"].get("read_only") is True
    assert "requires_tier_eval" not in tools["landing_page.generate"]
    # publish: go-live → the engine must decide the tier (parks for approval)
    assert tools["landing_page.publish"].get("requires_tier_eval") is True
    # generate is NOT untrusted-narrowing-safe: a run that ingested external content can't draft
    assert "landing_page.generate" not in m["untrusted_narrowing"]["allow"]


def test_publish_maps_to_its_action_generate_falls_back() -> None:
    # publish is governed by the pack rule keyed on the abstract action
    assert resolve_actions("landing_page.publish", {}) == ["action.landing_page.publish"]
    # generate has no mapping → falls back to the tool name (it's a no-tier tool anyway)
    assert resolve_actions("landing_page.generate", {}) == ["landing_page.generate"]


def test_allowlist_intersects_the_grant() -> None:
    # a tool granted by the pack (level-2) but absent from the archetype allowlist (level-1) is cut
    m = compile_manifest(
        instance_id=uuid.uuid4(), org_id=uuid.uuid4(),
        allowlist=["segments.query"], tool_grants=[_grant("landing_page.publish")])
    assert all(t["name"] != "landing_page.publish" for t in m["tools"])
