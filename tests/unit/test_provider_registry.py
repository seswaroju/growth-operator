"""Provider + model registry and adapters (PILOT-1B). Pure, no network, no DB."""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.runtime.adapters import ADAPTERS
from core.runtime.adapters.anthropic_native import AnthropicNativeAdapter
from core.runtime.adapters.base import NormalizedRequest
from core.runtime.adapters.openai_compatible import OpenAiCompatibleAdapter
from core.runtime.model_registry import (
    MODELS,
    CapabilityMismatch,
    ModelNotApproved,
    approved_models,
    estimate_cost,
    get_model,
    model_availability,
    require_capabilities,
)
from core.runtime.model_registry import validate_registry as validate_models
from core.runtime.providers import (
    PROVIDERS,
    ProviderNotConfigured,
    credential_for,
    get_provider_definition,
    provider_status,
)
from core.runtime.providers import validate_registry as validate_providers

REQ = NormalizedRequest(system="S", user="U", model="m", max_tokens=64)


# ---- Registry invariants -----------------------------------------------------------------------


def test_both_registries_are_well_formed() -> None:
    assert validate_providers() == []
    assert validate_models() == []


def test_endpoints_are_literal_https_hosts() -> None:
    """Endpoints are platform-controlled; a template or plain-http host is a misconfiguration."""
    for p in PROVIDERS:
        assert p.endpoint.startswith("https://") and "{" not in p.endpoint


def test_no_two_providers_share_a_credential() -> None:
    refs = [p.credential_ref for p in PROVIDERS]
    assert len(refs) == len(set(refs))


def test_the_adapter_belongs_to_the_provider_not_the_model() -> None:
    """A model cannot disagree with its provider about the wire protocol."""
    assert not any(hasattr(m, "adapter") for m in MODELS)
    for p in PROVIDERS:
        assert p.adapter in ADAPTERS


def test_openai_and_deepseek_share_one_adapter() -> None:
    """The point of the abstraction: a second vendor must not need a second adapter."""
    openai = get_provider_definition("openai")
    deepseek = get_provider_definition("deepseek")
    assert openai.adapter == deepseek.adapter == "openai_compatible"
    assert ADAPTERS[openai.adapter] is ADAPTERS[deepseek.adapter]
    assert openai.endpoint != deepseek.endpoint
    assert openai.credential_ref != deepseek.credential_ref


def test_anthropic_uses_its_native_adapter() -> None:
    assert get_provider_definition("anthropic").adapter == "anthropic_native"


def test_every_model_names_a_registered_provider() -> None:
    keys = {p.provider_key for p in PROVIDERS}
    for m in MODELS:
        assert m.provider in keys


def test_an_unknown_provider_fails_closed() -> None:
    with pytest.raises(ProviderNotConfigured) as exc:
        get_provider_definition("acme-ai")
    assert exc.value.reason == "provider_unknown"


def test_an_unknown_or_disabled_model_fails_closed() -> None:
    with pytest.raises(ModelNotApproved):
        get_model("openai", "gpt-9-ultra")


# ---- Credentials -------------------------------------------------------------------------------


def test_a_missing_credential_is_reported_without_naming_the_secret(monkeypatch) -> None:
    monkeypatch.delenv("GROWTH_OPERATOR_LLM_KEY_OPENAI", raising=False)
    status = provider_status("openai")
    assert status == "credential_missing"
    # the reason names no setting, path, endpoint or value
    assert "llm_key" not in status and "https" not in status


def test_each_provider_resolves_its_own_credential(monkeypatch) -> None:
    """A fallback authenticating with the primary's key would leak it to another vendor."""
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_KEY_OPENAI", "sk-openai")
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_KEY_DEEPSEEK", "sk-deepseek")
    monkeypatch.setenv("GROWTH_OPERATOR_LLM_KEY_ANTHROPIC", "sk-anthropic")
    assert credential_for(get_provider_definition("openai")) == "sk-openai"
    assert credential_for(get_provider_definition("deepseek")) == "sk-deepseek"
    assert credential_for(get_provider_definition("anthropic")) == "sk-anthropic"


# ---- Adapters ----------------------------------------------------------------------------------


def test_openai_compatible_builds_and_parses() -> None:
    a = OpenAiCompatibleAdapter()
    call = a.build(REQ, endpoint="https://api.example.com", key="sk-1")
    assert call.url == "https://api.example.com/v1/chat/completions"
    assert call.headers["Authorization"] == "Bearer sk-1"
    assert call.body["messages"][0]["role"] == "system"

    result = a.parse({"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                      "usage": {"prompt_tokens": 3, "completion_tokens": 5}})
    assert result.text == "hi" and result.usage.tokens_in == 3 and result.usage.tokens_out == 5


def test_anthropic_native_builds_and_parses() -> None:
    a = AnthropicNativeAdapter()
    call = a.build(REQ, endpoint="https://api.example.com", key="sk-2")
    assert call.url == "https://api.example.com/v1/messages"
    assert call.headers["x-api-key"] == "sk-2" and "Authorization" not in call.headers
    assert call.body["system"] == "S"

    result = a.parse({"content": [{"type": "text", "text": "hello"}],
                      "usage": {"input_tokens": 7, "output_tokens": 2}})
    assert result.text == "hello" and result.usage.tokens_in == 7


def test_the_same_adapter_produces_different_calls_per_provider() -> None:
    """One implementation, two vendors, two destinations and two credentials."""
    a = ADAPTERS["openai_compatible"]
    o = a.build(REQ, endpoint=get_provider_definition("openai").endpoint, key="sk-o")
    d = a.build(REQ, endpoint=get_provider_definition("deepseek").endpoint, key="sk-d")
    assert o.url != d.url
    assert o.headers["Authorization"] != d.headers["Authorization"]


def test_a_malformed_response_raises_rather_than_returning_empty_text() -> None:
    with pytest.raises(ValueError):
        OpenAiCompatibleAdapter().parse({"choices": []})
    with pytest.raises(ValueError):
        AnthropicNativeAdapter().parse({})


def test_no_adapter_accepts_an_endpoint_from_the_request() -> None:
    """Endpoints arrive as an explicit argument from the registry — never from request content."""
    import inspect

    for adapter in ADAPTERS.values():
        params = inspect.signature(adapter.build).parameters
        assert "endpoint" in params and params["endpoint"].kind == inspect.Parameter.KEYWORD_ONLY
    assert not hasattr(REQ, "endpoint") and not hasattr(REQ, "base_url")


# ---- Capabilities and cost ---------------------------------------------------------------------


def test_capability_mismatch_is_refused() -> None:
    """A node that needs something the model cannot do is refused BEFORE the call, not discovered
    mid-request.

    Uses vision as the missing capability. It used to use tool-calling on `deepseek-reasoner`,
    which no longer exists — and its V4 replacements do support tool calls, so the old assertion
    would have quietly stopped testing anything. Vision is a real gap: DeepSeek documents text,
    JSON output and tool calls, and the registry claims nothing beyond that."""
    flash = get_model("deepseek", "deepseek-v4-flash")
    with pytest.raises(CapabilityMismatch):
        require_capabilities(flash, frozenset({"vision"}))
    # The positive half: a model that HAS the capability is allowed through, so the test cannot
    # pass by refusing everything.
    require_capabilities(get_model("anthropic", "claude-sonnet-5"), frozenset({"vision"}))
    require_capabilities(flash, frozenset({"tool_calling"}))


def test_cost_uses_the_exact_model_not_the_provider() -> None:
    """gpt-5.6-sol and gpt-5-nano differ by more than an order of magnitude; the old per-provider
    table priced them identically."""
    big = estimate_cost(get_model("openai", "gpt-5.6-sol"), 1000, 1000)
    small = estimate_cost(get_model("openai", "gpt-5-nano"), 1000, 1000)
    assert big > small * 10


def test_every_approved_model_prices_input_and_output_separately() -> None:
    for m in approved_models():
        assert isinstance(m.cost_per_1k_in, Decimal)
        assert isinstance(m.cost_per_1k_out, Decimal)
        assert m.cost_per_1k_out >= m.cost_per_1k_in


# ---- Availability projection -------------------------------------------------------------------


def test_availability_reports_a_reason_without_leaking_configuration(monkeypatch) -> None:
    monkeypatch.delenv("GROWTH_OPERATOR_LLM_KEY_OPENAI", raising=False)
    reason = model_availability("openai", "gpt-5.6-sol")
    assert reason in ("ok", "credential_missing", "provider_disabled")
    assert "https://" not in reason and "llm_key" not in reason


def test_availability_distinguishes_unknown_model_from_unconfigured_provider() -> None:
    assert model_availability("openai", "not-a-model") == "model_unknown"


def test_the_fail_safe_chain_names_approved_models() -> None:
    """A fail-safe route pointing at an unapproved model id would itself be a config fault."""
    from core.runtime.routing import _FALLBACK_CHAIN

    for provider, model in _FALLBACK_CHAIN:
        get_model(provider, model)  # raises if unapproved


def test_the_configured_default_model_is_approved() -> None:
    """A default naming an unapproved id would fail every non-routing call at runtime — which is
    exactly what `claude-sonnet-4-5` did before PILOT-1B."""
    from core.common.config import get_settings

    settings = get_settings()
    get_model(settings.llm_provider, settings.llm_model)  # raises if unapproved


def test_the_evaluation_harness_cases_are_safe_to_run() -> None:
    """No PII, and every case pins a safety property rather than a stylistic preference."""
    import scripts.eval_models as harness

    for case in harness.CASES:
        assert case["id"] and case["message"]
        assert "@" not in case["message"] and "+91" not in case["message"]
