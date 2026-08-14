"""PILOT-1A — the model registry must describe vendors' CURRENT APIs.

A model id is a fact about someone else's live service, not something this repository can settle
among itself. Before this ticket the registry offered four models, two of which their vendor had
already retired — `claude-3-5-sonnet-20241022` (retired 2025-10-28) and `claude-3-5-haiku-20241022`
(retired 2026-02-19) — and four database routes pointed at them. The first request made with a real
API key would have failed, at the worst possible moment: during the first live smoke.

Migration 052 had already "fixed" those ids once by adding date suffixes, on the reasonable belief
that the suffix was the problem. It made them well-formed and no more callable.

Verified against vendor documentation on 2026-08-13.
"""

from __future__ import annotations

import pytest

from core.runtime.model_registry import (
    MODELS,
    RETIRED_MODEL_IDS,
    RETIRED_REPLACEMENTS,
    approved_models,
    current_models,
    get_model,
    is_retired,
    replacement_for,
    validate_registry,
)
from core.runtime.routing import _FALLBACK_CHAIN


def test_registry_has_no_structural_problems() -> None:
    assert validate_registry() == []


# ---- 21: retired ids are not selectable --------------------------------------------------------


@pytest.mark.parametrize("model", sorted(RETIRED_MODEL_IDS))
def test_a_retired_model_is_not_offered(model: str) -> None:
    """Not merely deprioritised — absent. A retired id is a guaranteed failure, so offering it at
    any priority is offering a broken choice."""
    assert model not in {m.model for m in MODELS}


@pytest.mark.parametrize("model", ["deepseek-chat", "deepseek-reasoner"])
def test_retired_deepseek_ids_are_gone(model: str) -> None:
    """DeepSeek retired both on 2026-07-24 in favour of the V4 family."""
    assert is_retired(model)
    with pytest.raises(Exception, match="model_unknown"):
        get_model("deepseek", model)


@pytest.mark.parametrize(
    "model", ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"])
def test_retired_anthropic_ids_are_gone(model: str) -> None:
    assert is_retired(model)
    with pytest.raises(Exception, match="model_unknown"):
        get_model("anthropic", model)


def test_every_retired_id_names_its_replacement() -> None:
    """So a route or config found pointing at one can be repaired, not just diagnosed."""
    assert set(RETIRED_MODEL_IDS) == set(RETIRED_REPLACEMENTS)
    for retired, replacement in RETIRED_REPLACEMENTS.items():
        assert not is_retired(replacement), f"{retired} is replaced by another retired model"
        assert replacement in {m.model for m in MODELS}


# ---- 22: current metadata validates ------------------------------------------------------------


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "deepseek-v4-pro"])
def test_current_deepseek_models_are_registered(model: str) -> None:
    definition = get_model("deepseek", model)
    assert definition.lifecycle == "current"
    # Both V4 models document a 1M context window.
    assert definition.max_context == 1_000_000
    # Both are served by the OpenAI-compatible endpoint Vaylorn already speaks — the reason this
    # refresh needed no transport change.
    from core.runtime.providers import get_provider_definition

    assert get_provider_definition("deepseek").adapter == "openai_compatible"


def test_deepseek_v4_pricing_is_not_the_old_chat_pricing() -> None:
    """The specific trap: copying the retired model's numbers onto its replacement produces a
    registry that looks refreshed and estimates costs wrongly."""
    from decimal import Decimal

    flash = get_model("deepseek", "deepseek-v4-flash")
    assert flash.cost_per_1k_in != Decimal("0.00027")   # deepseek-chat's old input price
    assert flash.cost_per_1k_out != Decimal("0.0011")   # deepseek-chat's old output price


def test_every_model_has_complete_cost_metadata() -> None:
    """Cost is why this registry exists; a model without it silently estimates as free."""
    for m in MODELS:
        assert m.cost_per_1k_in is not None and m.cost_per_1k_out is not None
        assert m.cost_per_1k_out >= m.cost_per_1k_in, f"{m.model}: output should not be cheaper"
        assert m.max_context and m.max_context > 0


def test_the_cheapest_current_candidates_are_available() -> None:
    """The pilot picks the cheapest model that clears the quality bar, so cheap current options
    must actually be offered."""
    cheap = {m.model for m in current_models() if m.quality_tier == "cheap"}
    assert {"gpt-5-nano", "deepseek-v4-flash"} <= cheap


def test_at_least_two_vendors_offer_a_current_model() -> None:
    """Cross-provider fallback is only real if a second vendor is actually usable."""
    assert len({m.provider for m in current_models()}) >= 2


# ---- lifecycle handling -------------------------------------------------------------------------


def test_deprecated_models_remain_callable_but_are_not_preferred() -> None:
    """gpt-4o and gpt-4o-mini are still listed by OpenAI, so an existing configuration keeps
    working; they are simply not what a new pilot should be steered onto."""
    deprecated = {m.model for m in MODELS if m.lifecycle == "deprecated"}
    assert deprecated == {"gpt-4o", "gpt-4o-mini"}
    assert deprecated <= {m.model for m in approved_models()}
    assert not deprecated & {m.model for m in current_models()}


def test_current_models_are_a_strict_subset_of_approved() -> None:
    assert set(current_models()) < set(approved_models()) or set(current_models()) == set(
        approved_models())


# ---- 23: nothing else still points at a retired model ------------------------------------------


def test_the_fallback_chain_names_only_current_models() -> None:
    """A fail-safe that names a retired model is not a fail-safe. Both entries used to."""
    for provider, model in _FALLBACK_CHAIN:
        assert not is_retired(model)
        assert get_model(provider, model).lifecycle == "current"


def test_the_fallback_chain_crosses_vendors() -> None:
    """Falling back to the same provider protects against nothing that took the primary down."""
    assert len({provider for provider, _ in _FALLBACK_CHAIN}) > 1


def test_the_configured_default_model_is_current() -> None:
    from core.common.config import Settings

    default = Settings(env="dev").llm_model
    assert not is_retired(default)
    assert default in {m.model for m in current_models()}


def test_no_source_file_still_references_a_retired_model() -> None:
    """The registry can be right while a seed script, a fallback or a docstring quietly is not."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for directory in ("core", "scripts", "verticals"):
        for path in (root / directory).rglob("*"):
            if path.suffix not in {".py", ".yaml", ".yml", ".md"} or "__pycache__" in str(path):
                continue
            if path.name == "model_registry.py":
                continue  # the one file that must name them, to refuse them
            text = path.read_text(errors="ignore")
            for retired in RETIRED_MODEL_IDS:
                if retired in text:
                    offenders.append(f"{path.relative_to(root)}: {retired}")
    assert not offenders, f"retired model ids still referenced: {offenders}"


def test_migration_054_repoints_every_retired_id_it_could_encounter() -> None:
    """Persisted routes are tenant-visible truth; §28 requires an explicit repoint rather than
    letting the first live request discover the problem."""
    from pathlib import Path

    migration = (Path(__file__).resolve().parents[2]
                 / "migrations/versions/d53fdc8c9b82_054_repoint_routes_off_retired_models.py")
    text = migration.read_text()
    for retired in ("claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022",
                    "deepseek-chat", "deepseek-reasoner"):
        assert retired in text, f"migration does not handle {retired}"
        assert replacement_for(retired) in text
