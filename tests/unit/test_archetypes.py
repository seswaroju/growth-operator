"""Archetype allowlist ↔ tool-permissions.yaml drift (MVP-020), no DB."""

from __future__ import annotations

import pathlib

import pytest
import yaml

from core.packs.archetypes import ARCHETYPE_ALLOWLISTS

_TOOL_PERMS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "docs" / "implementation" / "agents" / "tool-permissions.yaml"
)


def test_constants_match_tool_permissions_yaml_byte_for_byte() -> None:
    if not _TOOL_PERMS.exists():  # docs/ is a private-vault symlink, absent in CI
        pytest.skip("tool-permissions.yaml unavailable (private docs/ vault not checked out)")
    data = yaml.safe_load(_TOOL_PERMS.read_text())
    yaml_allowlists = data["archetype_allowlists"]
    # Dict + list equality → same archetypes, same tools, same order.
    assert ARCHETYPE_ALLOWLISTS == {k: list(v) for k, v in yaml_allowlists.items()}
