"""Provider registry — the approved inference vendors and how to reach them (PILOT-1B).

Vaylorn owns agent semantics, prompts, retrieval, tool authorization, commercial authorization,
approvals, safety, memory, routing and evaluation. A provider supplies inference and nothing else,
so vendor differences live here and never leak into agent or workflow code.

**Endpoints are platform-controlled.** A provider's URL comes from this registry — never from
operator input, tenant config, a model route, or model output. An operator selects an approved
*provider and model*, never a destination: a credential plus an attacker-chosen host is an
SSRF/credential-exfiltration primitive, and no UI or API in Vaylorn may offer one.

**Credentials are per provider.** A fallback must authenticate as itself; reusing the primary's key
against a different vendor would leak that key to a third party. `credential_ref` names a settings
field, and the secret itself is never returned by an API, written to a route, logged, or placed in
a prompt.

The adapter belongs to the **provider**, not the model — a model that disagreed with its provider
about the wire protocol would be unresolvable configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Adapter = Literal["openai_compatible", "anthropic_native"]


@dataclass(frozen=True)
class ProviderDefinition:
    provider_key: str
    adapter: Adapter
    endpoint: str          # platform-controlled base URL; never operator-supplied
    credential_ref: str    # settings field holding this provider's key — never the key itself
    enabled: bool = True


#: The approved vendors. DeepSeek is deliberately present in v1: it exercises the *same*
#: `openai_compatible` adapter as OpenAI against a different vendor, which is what proves the
#: transport is genuinely portable rather than OpenAI-shaped. Adding a further vendor whose protocol
#: is sufficiently compatible is a registration change here, not new transport code.
PROVIDERS: tuple[ProviderDefinition, ...] = (
    ProviderDefinition(
        "openai", "openai_compatible", "https://api.openai.com", "llm_key_openai"),
    ProviderDefinition(
        "deepseek", "openai_compatible", "https://api.deepseek.com", "llm_key_deepseek"),
    ProviderDefinition(
        "anthropic", "anthropic_native", "https://api.anthropic.com", "llm_key_anthropic"),
)

_BY_KEY: dict[str, ProviderDefinition] = {p.provider_key: p for p in PROVIDERS}


class ProviderNotConfigured(Exception):
    """A provider cannot be called: unknown, disabled, or missing its credential.

    This is a **configuration** fault, not a transient one. It is raised so a broken route becomes
    visible to Vaylorn Operations instead of being silently masked by fallback forever."""

    def __init__(self, provider_key: str, reason: str):
        self.provider_key = provider_key
        self.reason = reason  # provider_unknown | provider_disabled | credential_missing
        super().__init__(f"provider {provider_key!r} unavailable: {reason}")


def get_provider_definition(provider_key: str) -> ProviderDefinition:
    definition = _BY_KEY.get(provider_key)
    if definition is None:
        raise ProviderNotConfigured(provider_key, "provider_unknown")
    return definition


def credential_for(definition: ProviderDefinition) -> str:
    """The provider's own API key. Raises rather than falling back to another provider's key."""
    from core.common.config import get_settings

    key = getattr(get_settings(), definition.credential_ref, None)
    if not key:
        raise ProviderNotConfigured(definition.provider_key, "credential_missing")
    return str(key)


def provider_status(provider_key: str) -> str:
    """`ok` or a **non-sensitive** reason, for operator diagnostics.

    Deliberately returns a reason code and never the credential name, its value, a secret path or a
    stack trace — an operator needs to know *that* a provider is unconfigured, not how to reach it.
    """
    definition = _BY_KEY.get(provider_key)
    if definition is None:
        return "provider_unknown"
    if not definition.enabled:
        return "provider_disabled"
    try:
        credential_for(definition)
    except ProviderNotConfigured as exc:
        return exc.reason
    return "ok"


def is_callable(provider_key: str) -> bool:
    return provider_status(provider_key) == "ok"


def configured_providers() -> tuple[str, ...]:
    return tuple(p.provider_key for p in PROVIDERS if is_callable(p.provider_key))


def validate_registry() -> list[str]:
    """Structural invariants. Endpoint policy is enforced here so a bad edit fails CI, not prod."""
    problems: list[str] = []
    keys = [p.provider_key for p in PROVIDERS]
    if len(keys) != len(set(keys)):
        problems.append(f"duplicate provider keys: {sorted(keys)}")
    refs = [p.credential_ref for p in PROVIDERS]
    if len(refs) != len(set(refs)):
        problems.append("two providers share a credential_ref — keys must not be reused")
    for p in PROVIDERS:
        if not p.endpoint.startswith("https://"):
            problems.append(f"{p.provider_key}: endpoint must be https")
        if "{" in p.endpoint or " " in p.endpoint:
            problems.append(f"{p.provider_key}: endpoint must be a literal host, not a template")
        if p.adapter not in ("openai_compatible", "anthropic_native"):
            problems.append(f"{p.provider_key}: unknown adapter {p.adapter!r}")
        if not p.credential_ref.startswith("llm_key_"):
            problems.append(f"{p.provider_key}: credential_ref must name an llm_key_* setting")
    return problems
