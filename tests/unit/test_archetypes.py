"""Archetype allowlist ↔ tool-permissions.yaml drift (MVP-020), no DB."""

from __future__ import annotations

import pathlib

import yaml

from core.packs.archetypes import ARCHETYPE_ALLOWLISTS

_TOOL_PERMS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "docs" / "implementation" / "agents" / "tool-permissions.yaml"
)


def test_constants_match_tool_permissions_yaml_byte_for_byte() -> None:
    data = yaml.safe_load(_TOOL_PERMS.read_text())
    yaml_allowlists = data["archetype_allowlists"]
    # Dict + list equality → same archetypes, same tools, same order.
    assert ARCHETYPE_ALLOWLISTS == {k: list(v) for k, v in yaml_allowlists.items()}
