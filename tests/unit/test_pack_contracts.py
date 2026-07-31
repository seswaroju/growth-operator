"""Pack contract models (MVP-038).

Asserts every real pack file under `verticals/<pack>/` validates against its contract, and
that a single wrong field produces a path-precise error. Pure — loads files and validates;
no DB. Prompt `.md` layers are parsed by the anchor splitter (MVP-039) and the WhatsApp
templates seed (`templates/`) belongs to MVP-035, so both are excluded from the contract walk.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from core.packs.contracts import (
    AgentBinding,
    BindingsPack,
    CalendarPack,
    CatalogSchema,
    EvalSuite,
    IntegrationSpec,
    OnboardingPack,
    PackManifest,
    PricingStrategyDef,
    UiPack,
    WorkflowDef,
)

VERTICALS = Path(__file__).resolve().parents[2] / "verticals"
PACKS = [p for p in ("jewelry", "kirana") if (VERTICALS / p).is_dir()]


def _load(path: Path) -> Any:
    if path.suffix == ".json":
        return json.loads(path.read_text())
    return yaml.safe_load(path.read_text())


# Dispatch a file to the contract that validates it. Returns None to skip (md / templates).
def _validator_for(path: Path) -> Callable[[dict], BaseModel] | None:
    parent = path.parent.name
    rel = path.name
    if path.suffix == ".md" or parent == "templates":
        return None
    if rel == "pack.yaml":
        return PackManifest.model_validate
    if parent == "agents":
        return BindingsPack.model_validate
    if parent == "catalog":
        return CatalogSchema.from_document
    if parent == "pricing":
        return PricingStrategyDef.model_validate
    if parent == "workflows":
        return WorkflowDef.model_validate
    if parent == "integrations":
        return IntegrationSpec.model_validate
    if parent == "onboarding":
        return OnboardingPack.model_validate
    if parent == "ui":
        return UiPack.model_validate
    if parent == "calendar":
        return CalendarPack.model_validate
    if parent == "evals":
        return EvalSuite.model_validate
    return None


def _contract_files() -> list[Path]:
    files: list[Path] = []
    for pack in PACKS:
        for path in sorted((VERTICALS / pack).rglob("*")):
            if path.is_file() and _validator_for(path) is not None:
                files.append(path)
    return files


@pytest.mark.skipif(not PACKS, reason="no vertical packs present")
@pytest.mark.parametrize("path", _contract_files(), ids=lambda p: str(p.relative_to(VERTICALS)))
def test_every_pack_file_parses(path: Path) -> None:
    validator = _validator_for(path)
    assert validator is not None
    validator(_load(path))  # raises ValidationError if the file violates its contract


@pytest.mark.parametrize("pack", PACKS)
def test_manifest_fields(pack: str) -> None:
    m = PackManifest.model_validate(_load(VERTICALS / pack / "pack.yaml"))
    assert m.pack == pack and m.version and m.platform_api
    assert m.slots  # both packs declare L2 slots


@pytest.mark.parametrize("pack", PACKS)
def test_bindings_and_catalog(pack: str) -> None:
    b = BindingsPack.model_validate(_load(VERTICALS / pack / "agents" / "bindings.yaml"))
    assert b.bindings and all(isinstance(x, AgentBinding) for x in b.bindings)
    c = CatalogSchema.from_document(_load(VERTICALS / pack / "catalog" / "schema.json"))
    assert c.version >= 1 and c.json_schema.get("type") == "object" and c.identity_keys


# ---- Negative fixtures: one wrong field → path-precise error --------------------------


def test_unknown_manifest_field_is_path_precise() -> None:
    raw = _load(VERTICALS / PACKS[0] / "pack.yaml")
    raw["riskclass"] = "standard"  # typo of risk_class
    with pytest.raises(ValidationError) as ei:
        PackManifest.model_validate(raw)
    assert "riskclass" in str(ei.value) and "extra" in str(ei.value).lower()


def test_bad_binding_tier_names_the_path() -> None:
    raw = _load(VERTICALS / PACKS[0] / "agents" / "bindings.yaml")
    raw["bindings"][0]["tier_defaults"][0]["tier"] = "high"  # must be int
    with pytest.raises(ValidationError) as ei:
        BindingsPack.model_validate(raw)
    loc = {tuple(e["loc"]) for e in ei.value.errors()}
    assert any("tier" in str(x) and "tier_defaults" in str(x) for x in loc)


def test_workflow_missing_required_field() -> None:
    with pytest.raises(ValidationError) as ei:
        WorkflowDef.model_validate({"version": 1, "trigger": {}, "steps": []})
    assert any(e["loc"] == ("workflow",) and e["type"] == "missing" for e in ei.value.errors())


def test_pricing_engine_enum_enforced() -> None:
    with pytest.raises(ValidationError) as ei:
        PricingStrategyDef.model_validate({"strategy_key": "x", "engine": "python"})
    assert any(e["loc"] == ("engine",) for e in ei.value.errors())
