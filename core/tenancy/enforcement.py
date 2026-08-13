"""Enforcement inventory — every execution surface a sellable capability governs (PLAN-5).

The problem this solves is drift: a capability becomes sellable, one obvious route gets a gate, and
a second path — a worker, an agent tool, a resumed run — quietly keeps working. Recording the
surfaces per capability and checking them in CI makes partial coverage **visible** rather than
letting it pass.

Two rules decide whether a surface is gated, and neither is "what HTTP verb is it":

*Entitlement governs paid execution, not data ownership.* Automation, mutations, external effects
and paid computation require a current plan grant. Reading the tenant's own historical records,
exporting them, or exercising privacy rights does not — a merchant who cancels keeps visibility of
what they already have, and simply cannot make the product do more work. Authentication and RBAC
govern that access, exactly as PLAN-2 states.

*RBAC and entitlement are independent.* Every gated surface still requires its role permission;
neither check substitutes for the other.

Each surface therefore declares either an `enforcement` mechanism **plus the test that proves it**,
or an `exemption_reason` recording why it is deliberately RBAC-only. `missing` and `unknown` are not
expressible — a surface without one of the two fails the CI guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

Kind = Literal["http", "tool", "job", "service", "executor"]
Action = Literal[
    "historical_data_read",   # the merchant's own stored records
    "paid_compute",           # fresh premium computation
    "mutation",               # changes tenant state
    "external_effect",        # reaches a customer or a third party
    "automation",             # the platform acting on the tenant's behalf
    "maintenance",            # cleanup / integrity / safety
    "account_privacy",        # account, billing, export, erasure
]


@dataclass(frozen=True)
class Surface:
    id: str
    kind: Kind
    action: Action
    enforcement: str | None = None      # how it is gated
    test: str | None = None             # the test that proves the gate
    exemption_reason: str | None = None  # why it is deliberately not gated
    routes: tuple[str, ...] = ()        # "METHOD /path" for http surfaces — bound, not guessed

    @property
    def is_gated(self) -> bool:
        return self.enforcement is not None


@dataclass(frozen=True)
class CapabilityEnforcement:
    capability: str
    surfaces: tuple[Surface, ...]


def _gate(id_: str, kind: Kind, action: Action, enforcement: str, test: str,
          *routes: str) -> Surface:
    return Surface(id_, kind, action, enforcement=enforcement, test=test, routes=routes)


def _exempt(id_: str, kind: Kind, action: Action, reason: str, *routes: str) -> Surface:
    return Surface(id_, kind, action, exemption_reason=reason, routes=routes)


#: Routers whose every operation must be classified. A new route under one of these prefixes fails
#: CI until it is bound to a surface — the guard that stops "sellable but ungated" recurring.
MAPPED_PREFIXES: tuple[str, ...] = (
    "/v1/campaigns", "/v1/landing", "/v1/imports", "/v1/catalog", "/v1/leads", "/v1/rates", "/p/",
)


# Reasons are written once and reused so the rationale cannot drift between surfaces.
_OWN_RECORDS = ("the merchant's own stored records — entitlement governs paid execution, not "
                "visibility of data they already own (founder ruling 2026-08-13)")
_PRIVACY = "account/privacy right — never contingent on billing state"
_SAFETY = ("resolving a pending approval must stay possible; the send it authorises is gated "
           "downstream at the mediation boundary")

INVENTORY: tuple[CapabilityEnforcement, ...] = (
    CapabilityEnforcement("conversations", (
        _exempt("http.conversations.list", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/conversations"),
        _exempt("http.conversations.thread", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/conversations/{conversation_id}"),
        _exempt("http.approvals.queue", "http", "maintenance", _SAFETY,
              "GET /v1/approvals"),
        _exempt("http.approvals.resolve", "http", "maintenance", _SAFETY,
              "POST /v1/approvals/{approval_id}/resolve"),
        _gate("tool.messages.send", "tool", "external_effect",
              "mediation TOOL_CAPABILITY", "test_mediation_enforcement.py::test_tool_denied"),
    )),
    CapabilityEnforcement("catalog", (
        _exempt("http.catalog.items.list", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/catalog/items"),
        _exempt("http.catalog.items.get", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/catalog/items/{item_id}"),
        _gate("http.catalog.search", "http", "paid_compute",
              "requires_feature", "test_runtime_enforcement.py::test_catalog_search_gated",
              "GET /v1/catalog/search"),
        _gate("http.catalog.items.create", "http", "mutation",
              "requires_feature", "test_runtime_enforcement.py::test_catalog_write_gated",
              "POST /v1/catalog/items"),
        _gate("http.catalog.items.update", "http", "mutation",
              "requires_feature", "test_runtime_enforcement.py::test_catalog_write_gated",
              "PATCH /v1/catalog/items/{item_id}"),
        _gate("http.catalog.items.archive", "http", "mutation",
              "requires_feature", "test_runtime_enforcement.py::test_catalog_write_gated",
              "DELETE /v1/catalog/items/{item_id}"),
        _gate("tool.catalog.search", "tool", "paid_compute",
              "mediation TOOL_CAPABILITY", "test_mediation_enforcement.py::test_tool_denied"),
    )),
    CapabilityEnforcement("customers", (
        _exempt("http.customers.list", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/customers"),
        _exempt("http.customers.profile", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/customers/{contact_id}"),
        _exempt("http.customers.timeline", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/customers/{contact_id}/timeline"),
        _exempt("http.customers.notes", "http", "mutation",
                "annotating your own records is data upkeep, not paid execution",
              "GET /v1/customers/{contact_id}/notes", "POST /v1/customers/{contact_id}/notes"),
        _exempt("http.customers.tags", "http", "mutation",
                "annotating your own records is data upkeep, not paid execution",
              "GET /v1/customers/{contact_id}/tags",
              "POST /v1/customers/{contact_id}/tags",
              "DELETE /v1/customers/{contact_id}/tags/{tag}"),
        _exempt("http.customers.export", "http", "account_privacy", _PRIVACY,
              "GET /v1/customers/{contact_id}/export"),
        _exempt("http.customers.erase", "http", "account_privacy", _PRIVACY,
              "DELETE /v1/customers/{contact_id}"),
    )),
    CapabilityEnforcement("ghost_recovery", (
        _exempt("http.leads.list", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/leads"),
        _gate("http.leads.recovery", "http", "automation",
              "requires_feature", "test_runtime_enforcement.py::test_recovery_override_gated",
              "POST /v1/leads/{lead_id}/recovery"),
        _gate("job.recovery_sweep", "job", "automation",
              "assert_entitled per org",
              "test_job_enforcement.py::test_recovery_sweep_skips_unentitled_org"),
    )),
    CapabilityEnforcement("campaigns.whatsapp", (
        _exempt("http.campaigns.list", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/campaigns"),
        _exempt("http.campaigns.get", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/campaigns/{campaign_id}"),
        _gate("http.campaigns.create", "http", "mutation",
              "requires_feature", "test_runtime_enforcement.py::test_campaign_create_gated",
              "POST /v1/campaigns"),
        _gate("http.campaigns.send", "http", "external_effect",
              "requires_feature", "test_runtime_enforcement.py::test_campaign_send_gated",
              "POST /v1/campaigns/{campaign_id}/send"),
        _gate("http.campaigns.audience_preview", "http", "paid_compute",
              "requires_feature", "test_runtime_enforcement.py::test_audience_preview_gated",
              "GET /v1/campaigns/audience-preview"),
        _gate("job.campaign_fanout", "job", "external_effect",
              "assert_entitled per campaign; halts once",
              "test_job_enforcement.py::test_fanout_halts_revoked_campaign"),
    )),
    CapabilityEnforcement("campaigns.analytics", (
        _gate("http.campaigns.analytics", "http", "paid_compute",
              "requires_feature", "test_runtime_enforcement.py::test_campaign_analytics_gated",
              "GET /v1/campaigns/{campaign_id}/analytics"),
        _gate("http.campaigns.report", "http", "paid_compute",
              "requires_feature", "test_runtime_enforcement.py::test_campaign_report_gated",
              "POST /v1/campaigns/{campaign_id}/report"),
    )),
    CapabilityEnforcement("landing_pages", (
        _exempt("http.landing.pages.list", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/landing/pages"),
        _exempt("http.landing.pages.get", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/landing/pages/{page_id}"),
        _exempt("http.landing.pages.preview", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/landing/pages/{page_id}/preview"),
        _exempt("http.landing.pages.variants", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/landing/pages/{page_id}/variants"),
        _exempt("http.landing.pages.version_preview", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/landing/pages/{page_id}/versions/{version_no}/preview"),
        _gate("http.landing.pages.create", "http", "paid_compute",
              "requires_feature", "test_runtime_enforcement.py::test_landing_create_gated",
              "POST /v1/landing/pages"),
        _gate("http.landing.pages.from_upload", "http", "paid_compute",
              "requires_feature", "test_runtime_enforcement.py::test_landing_create_gated",
              "POST /v1/landing/pages/from-upload"),
        _gate("http.landing.pages.select", "http", "mutation",
              "requires_feature", "test_runtime_enforcement.py::test_landing_lifecycle_gated",
              "POST /v1/landing/pages/{page_id}/select"),
        _gate("http.landing.pages.submit", "http", "mutation",
              "requires_feature", "test_runtime_enforcement.py::test_landing_lifecycle_gated",
              "POST /v1/landing/pages/{page_id}/submit"),
        _gate("http.landing.pages.publish", "http", "external_effect",
              "requires_feature", "test_runtime_enforcement.py::test_landing_lifecycle_gated",
              "POST /v1/landing/pages/{page_id}/publish"),
        _gate("http.landing.pages.pause", "http", "mutation",
              "requires_feature", "test_runtime_enforcement.py::test_landing_lifecycle_gated",
              "POST /v1/landing/pages/{page_id}/pause"),
        _gate("http.landing.pages.rollback", "http", "mutation",
              "requires_feature", "test_runtime_enforcement.py::test_landing_lifecycle_gated",
              "POST /v1/landing/pages/{page_id}/rollback"),
        _gate("http.landing.pages.archive", "http", "mutation",
              "requires_feature", "test_runtime_enforcement.py::test_landing_lifecycle_gated",
              "POST /v1/landing/pages/{page_id}/archive"),
        _gate("http.landing.pages.insights", "http", "paid_compute",
              "requires_feature", "test_runtime_enforcement.py::test_landing_insights_gated",
              "GET /v1/landing/pages/{page_id}/insights"),
        # The hosted public runtime is an ongoing paid service, not a historical read.
        _gate("service.landing.published_spec", "service", "external_effect",
              "assert_entitled inside the service; neutral 404",
              "test_landing_public_enforcement.py::test_public_page_not_served",
              "GET /p/{page_id}"),
        _gate("service.landing.record_public_event", "service", "paid_compute",
              "is_entitled inside the service; neutral 204, records nothing",
              "test_landing_public_enforcement.py::test_track_records_nothing",
              "POST /v1/landing/track"),
        _gate("service.landing.capture_lead", "service", "external_effect",
              "is_entitled inside the service; neutral response, no PII stored",
              "test_landing_public_enforcement.py::test_lead_not_captured",
              "POST /p/{page_id}/lead"),
        _gate("tool.landing_page.generate", "tool", "paid_compute",
              "mediation TOOL_CAPABILITY", "test_mediation_enforcement.py::test_tool_denied"),
        _gate("tool.landing_page.publish", "tool", "external_effect",
              "mediation TOOL_CAPABILITY", "test_mediation_enforcement.py::test_tool_denied"),
    )),
    CapabilityEnforcement("catalog.ingestion", (
        _exempt("http.imports.list", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/imports"),
        _exempt("http.imports.get", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/imports/{batch_id}"),
        _exempt("http.imports.rows", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/imports/{batch_id}/rows"),
        _exempt("http.imports.stream", "http", "historical_data_read", _OWN_RECORDS,
              "GET /v1/imports/{batch_id}/stream"),
        _gate("http.imports.create", "http", "paid_compute",
              "requires_feature", "test_runtime_enforcement.py::test_import_create_gated",
              "POST /v1/imports"),
        _gate("http.imports.extract", "http", "paid_compute",
              "requires_feature", "test_runtime_enforcement.py::test_import_stage_gated",
              "POST /v1/imports/{batch_id}/extract"),
        _gate("http.imports.validate", "http", "paid_compute",
              "requires_feature", "test_runtime_enforcement.py::test_import_stage_gated",
              "POST /v1/imports/{batch_id}/validate"),
        _gate("http.imports.row_write", "http", "mutation",
              "requires_feature", "test_runtime_enforcement.py::test_import_stage_gated",
              "POST /v1/imports/{batch_id}/rows/confirm-all",
              "PATCH /v1/imports/{batch_id}/rows/{seq}",
              "POST /v1/imports/{batch_id}/rows/{seq}/confirm",
              "POST /v1/imports/{batch_id}/rows/{seq}/reject"),
        _gate("service.ingestion.load", "service", "mutation",
              "assert_entitled inside the service",
              "test_runtime_enforcement.py::test_import_load_service_gated",
              "POST /v1/imports/{batch_id}/load"),
        _gate("service.ingestion.revert", "service", "mutation",
              "assert_entitled inside the service",
              "test_runtime_enforcement.py::test_import_revert_service_gated",
              "POST /v1/imports/{batch_id}/revert"),
        _exempt("job.import_batch_reaper", "job", "maintenance",
                "storage hygiene — deleting stale rows and blob refs must survive cancellation"),
    )),
)


_VERTICALS_ROOT = Path(__file__).resolve().parents[2] / "verticals"


@lru_cache(maxsize=4)
def _pack_inventory(root: str) -> tuple[CapabilityEnforcement, ...]:
    """Surfaces contributed by vertical packs, read by path — `core/` never names a vertical."""
    import yaml

    out: list[CapabilityEnforcement] = []
    base = Path(root)
    if not base.is_dir():
        return ()
    for manifest in sorted(base.glob("*/pack.yaml")):
        data = yaml.safe_load(manifest.read_text()) or {}
        slug = str(data.get("pack") or manifest.parent.name)
        path = manifest.parent / "commercial" / "enforcement.yaml"
        if not path.is_file():
            continue
        for key, body in sorted((yaml.safe_load(path.read_text()) or {}).items()):
            surfaces = tuple(
                Surface(
                    id=str(x["id"]), kind=x["kind"], action=x["action"],
                    enforcement=x.get("enforcement"), test=x.get("test"),
                    exemption_reason=x.get("exemption_reason"),
                    routes=tuple(x.get("routes") or ()),
                )
                for x in (body or {}).get("surfaces", [])
            )
            out.append(CapabilityEnforcement(f"{slug}.{key}", surfaces))
    return tuple(out)


def inventory(*, root: Path | None = None) -> tuple[CapabilityEnforcement, ...]:
    """Platform surfaces plus every pack contribution."""
    return INVENTORY + _pack_inventory(str(root or _VERTICALS_ROOT))


def capability_surfaces(capability: str) -> tuple[Surface, ...]:
    for entry in inventory():
        if entry.capability == capability:
            return entry.surfaces
    return ()


def all_surfaces() -> tuple[tuple[str, Surface], ...]:
    return tuple((e.capability, s) for e in inventory() for s in e.surfaces)


def validate_inventory() -> list[str]:
    """Every declared surface must be classified. Returns violations; empty means complete.

    Deliberately stricter than "each capability has at least one gate": partial coverage is the
    failure mode this exists to catch, since a capability can look enforced while several of its
    surfaces stay open."""
    from core.tenancy.capabilities import by_key, catalog

    problems: list[str] = []
    entries = inventory()
    covered = {e.capability for e in entries}
    for cap in catalog():
        if cap.runtime_grantable and cap.key not in covered:
            problems.append(f"{cap.key}: sellable but absent from the enforcement inventory")
    for entry in entries:
        if by_key(entry.capability) is None:
            problems.append(f"{entry.capability}: not a canonical capability")
        if not entry.surfaces:
            problems.append(f"{entry.capability}: declares no execution surface")
        seen: set[str] = set()
        for s in entry.surfaces:
            w = f"{entry.capability}/{s.id}:"
            if s.id in seen:
                problems.append(f"{w} duplicate surface id")
            seen.add(s.id)
            if s.enforcement and s.exemption_reason:
                problems.append(f"{w} is both enforced and exempt — pick one")
            elif s.enforcement:
                if not s.test:
                    problems.append(f"{w} enforced without a named test")
            elif s.exemption_reason:
                if len(s.exemption_reason) < 20:
                    problems.append(f"{w} exemption reason is too thin to review")
            else:
                problems.append(f"{w} is neither enforced nor explicitly exempt")
    return problems
