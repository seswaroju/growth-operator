"""L0↔L1 pack contracts (MVP-038).

Packs are **data** validated against these typed models — `core/` never imports pack code
(Rule Zero). Every file under `verticals/<pack>/` parses into one of these; a wrong or
misspelled field yields a path-precise pydantic error (the acceptance criterion). This module
is pure: zero I/O. Callers load the YAML/JSON and pass the dict to `model_validate` (or the
`from_document` helpers where a file's on-disk layout differs from the contract).

Signatures follow docs/21-platform/core-platform.md. Where the authoritative pack **data**
deviates from those illustrative signatures, the data wins so every file parses, and the
deviation is recorded in DECISIONS.md (2026-07-31): `kpi_defs`→`kpis`+`budgets`,
`rule_schema`/`rate_source_requirements`→`rules`/`rate_sources`, `identity_keys` is a list of
composite key-lists, `mcp_server` may be a bare string. Models are **strict** (extra forbidden)
where the platform owns the shape, and **open** where the pack or a downstream engine does
(integrations, pricing rules, onboarding, ui, evals).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Archetype = Literal["concierge", "nurture", "campaigner", "ops", "support", "planner"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


# --- Manifest (pack.yaml) --------------------------------------------------------------


class SlotSpec(_Open):
    """A JSON-Schema-ish L2 tenant slot (type + validation keywords + ui hints)."""

    type: str


class PackRequires(_Strict):
    channels: list[str] = []
    optional_channels: list[str] = []
    capabilities: list[str] = []
    rate_sources: list[str] = []


class PackProvides(_Strict):
    archetypes: list[str] = []
    pricing_strategy: str | None = None
    catalog_schema: str | None = None


class Signing(_Strict):
    alg: str
    key_id: str


class PackManifest(_Strict):
    pack: str
    version: str
    platform_api: str
    display_name: str | None = None
    description: str | None = None
    locales: list[str] = []
    risk_class: str | None = None
    requires: PackRequires = PackRequires()
    provides: PackProvides = PackProvides()
    slots: dict[str, SlotSpec] = {}
    onboarding: str | None = None
    ui: str | None = None
    calendar_pack: str | None = None
    commercial: str | None = None   # PLAN-1: optional vertical commercial capabilities
    evals_required: list[str] = []
    signing: Signing | None = None


# --- Agent bindings (agents/bindings.yaml) ---------------------------------------------


class PromptLayerRef(_Strict):
    ref: str          # e.g. "prompts/concierge.md#qualify"
    version: str      # e.g. "3.x"


class TaskDef(_Strict):
    task: str
    intents: list[str] = []
    prompt_layer: PromptLayerRef


class ToolGrant(_Strict):
    name: str
    rate_limit: dict[str, Any] | None = None
    params_constraints: dict[str, Any] | None = None


class PolicyRuleRef(_Strict):
    rule_key: str
    applies_to: str
    condition: str
    tier: int
    description: str | None = None
    approver: str | None = None
    timeout: str | None = None
    on_timeout: str | None = None
    confirm: str | None = None


class AgentBinding(_Strict):
    archetype: Archetype
    persona_default: str
    tasks: list[TaskDef] = []
    tool_grants: list[ToolGrant] = []
    tier_defaults: list[PolicyRuleRef] = []
    kpis: list[str] = []
    budgets: dict[str, int] = {}


class BindingsPack(_Strict):
    bindings: list[AgentBinding]
    planner: dict[str, Any] | None = None  # planner config is freeform (frequency cap, digest)


# --- Catalog schema (catalog/schema.json) ----------------------------------------------


class CatalogSchema(_Strict):
    json_schema: dict[str, Any]           # draft 2020-12 + x-index/x-search/x-pii annotations
    search_projection: list[str] = []
    identity_keys: list[list[str]] = []   # each inner list is one composite identity key
    version: int = 1

    @classmethod
    def from_document(cls, raw: dict[str, Any]) -> CatalogSchema:
        """Split a catalog schema document (a JSON Schema plus search/identity keys) into the
        contract fields. `version` comes from the trailing segment of `$id`."""
        doc = dict(raw)
        search_projection = doc.pop("search_projection", [])
        identity_keys = doc.pop("identity_keys", [])
        tail = str(doc.get("$id", "")).rsplit(":", 1)[-1]
        version = int(tail) if tail.isdigit() else 1
        return cls(
            json_schema=doc, search_projection=search_projection,
            identity_keys=identity_keys, version=version,
        )


# --- Pricing strategy (pricing/strategy.yaml) ------------------------------------------


class PricingStrategyDef(_Open):
    """Open: rules/rate_sources/tax_rules are engine-specific (validated by the pricing
    engine, MVP-050+). Only the platform-owned identity fields are required here."""

    strategy_key: str
    engine: Literal["rules_v1", "wasm"]
    input_schema: dict[str, Any] | None = None
    wasm_module_uri: str | None = None


# --- Workflows (workflows/*.yaml) ------------------------------------------------------


class WorkflowDef(_Strict):
    workflow: str
    version: int
    trigger: dict[str, Any]
    steps: list[dict[str, Any]]
    guards: list[Any] = []   # guard expressions (CEL strings) or refs — see workflow-engine.md
    concurrency: dict[str, Any] | None = None
    compensation: list[dict[str, Any]] | dict[str, Any] | None = None


# --- Integrations (integrations/*.yaml) ------------------------------------------------


class IntegrationSpec(_Open):
    """Open: each provider carries its own extra config (calendars, sources_ref, usage,
    templates_namespace, …). `mcp_server` may be a bare server name or a ref object."""

    integration: str
    mcp_server: str | dict[str, Any] | None = None
    credential_schema: dict[str, Any] | None = None
    scopes: list[str] = []
    health_check: dict[str, Any] | None = None


# --- Auxiliary packs (onboarding / ui / calendar / evals) ------------------------------


class OnboardingStep(_Open):
    id: str


class OnboardingPack(_Strict):
    steps: list[OnboardingStep]


class UiPack(_Open):
    """Render templates + copy slots + dashboard widgets — freeform, consumed by the UI layer."""


class CalendarEvent(_Strict):
    key: str
    date: date
    campaign_window_days: int
    kind: str | None = None


class CalendarPack(_Strict):
    events: list[CalendarEvent]
    recurrence: dict[str, Any] | None = None


class CapabilityDef(_Strict):
    """A vertical-specific commercial capability contributed by a pack (PLAN-1).

    `key` is namespaced to `<pack>.<key>` on load, so a pack can never shadow an L0 capability.
    Declaring one here puts it in the **global** catalog only — it never becomes effective for a
    tenant that has not installed the pack (PLAN-2 owns that filtering)."""

    key: str
    label: str
    description: str
    category: str
    kind: Literal["feature", "agent", "channel", "channel_capability", "addon", "limit"]
    status: Literal["available", "beta", "partial", "planned"]
    commercial_visibility: Literal["public", "public_beta", "private_beta", "internal", "planned"]
    runtime_grantable: bool = False
    enforced_by: str | None = None
    evidence_refs: list[str] = []
    depends_on: list[str] = []


class CommercialPack(_Strict):
    capabilities: list[CapabilityDef] = []


class EvalSuite(_Open):
    suite: str
    pass_bar: dict[str, Any]
    cases: list[dict[str, Any]] = []


# --- Contracts with no standalone pack file yet (modelled for later tickets) -----------


class CompliancePack(_Open):
    consent_scripts: dict[str, Any] = {}
    blocked_actions: list[str] = []
    disclosures: list[dict[str, Any]] = []
    retention_overrides: list[dict[str, Any]] = []
    tax_rules: list[dict[str, Any]] = []


class KPIPack(_Open):
    metrics: list[dict[str, Any]] = []
    digest_layout: dict[str, Any] = {}
    pilot_success_bar: dict[str, float] = {}


class PromptLayerDef(_Strict):
    """One prompt layer record — produced by the anchor splitter (MVP-039) from a `.md` file."""

    archetype: str
    task: str
    version: str
    content: str
    params_schema: dict[str, Any] | None = None
    requires: dict[str, str] = {}
