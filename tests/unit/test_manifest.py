"""Manifest compiler + signing (MVP-061) — pure, no DB.

The compiled tool surface is the archetype ∩ pack ∩ tenant intersection; read-only tools skip the
tier gate; the manifest is ed25519-signed and any tamper (tool, hash, or signature) fails verify.
"""

from __future__ import annotations

import uuid

from core.mediation import manifest as m


def _compile(**kw: object) -> dict:
    base = {"instance_id": uuid.uuid4(), "org_id": uuid.uuid4(),
            "allowlist": ["catalog.search", "messages.send", "pricing.compute"],
            "tool_grants": [{"name": "catalog.search", "rate_limit": {"per_min": 120}},
                            {"name": "messages.send"},
                            {"name": "crm.write"}]}  # crm.write not in the allowlist
    base.update(kw)
    return m.compile_manifest(**base)  # type: ignore[arg-type]


def test_intersection_is_archetype_and_pack() -> None:
    tools = {t["name"] for t in _compile()["tools"]}
    assert tools == {"catalog.search", "messages.send"}  # crm.write dropped (not in allowlist)


def test_tenant_grants_narrow_further() -> None:
    tools = {t["name"] for t in _compile(tenant_allow={"catalog.search"})["tools"]}
    assert tools == {"catalog.search"}  # tenant restricted to one


def test_read_only_skips_tier_and_others_require_eval() -> None:
    tools = {t["name"]: t for t in _compile()["tools"]}
    assert tools["catalog.search"].get("read_only") is True
    assert "requires_tier_eval" not in tools["catalog.search"]
    assert tools["messages.send"].get("requires_tier_eval") is True
    assert "read_only" not in tools["messages.send"]


def test_untrusted_narrowing_allows_read_only_tools() -> None:
    man = _compile()
    assert man["untrusted_narrowing"]["allow"] == ["catalog.search"]


def test_sign_then_verify_roundtrips() -> None:
    signed = m.sign(_compile())
    assert signed["hash"].startswith("sha256:") and signed["signature"].startswith("ed25519:")
    assert m.verify(signed) is True
    assert m.manifest_hash(signed) == signed["hash"].removeprefix("sha256:")


def test_tampering_any_part_fails_verify() -> None:
    signed = m.sign(_compile())

    forged_tool = dict(signed)
    forged_tool["tools"] = [{"name": "evil.tool", "requires_tier_eval": True}]
    assert m.verify(forged_tool) is False  # body changed, hash/sig no longer match

    bad_hash = dict(signed)
    bad_hash["hash"] = "sha256:deadbeef"
    assert m.verify(bad_hash) is False

    bad_sig = dict(signed)
    bad_sig["signature"] = "ed25519:AAAA"
    assert m.verify(bad_sig) is False
