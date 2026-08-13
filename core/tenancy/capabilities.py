"""Canonical capability catalog — the global product vocabulary (PLAN-1).

**This module is global and org-independent.** No function here takes an `org_id` or an
`AsyncSession`, and nothing here queries the database. Living under `core/tenancy/` is a packaging
decision, not a scoping one:

    capabilities.py   canonical/global product vocabulary  — what capabilities EXIST
    entitlements.py   tenant-specific effective-access resolver — what a store may USE

Global catalog knowledge is **not** tenant entitlement. A capability contributed by a vertical pack
may appear here while remaining unreachable for a tenant that has not installed that pack; PLAN-2's
resolver is what filters vertical capabilities against a tenant's installed packs.

Four concepts are deliberately kept apart, because conflating them is how a pricing page ends up
promising something the product cannot do:

    status                  engineering/product maturity
    commercial_visibility   eligibility for commercial presentation
    effective entitlement   whether this tenant bought / was granted it (entitlements.py)
    operational readiness   whether this deployment/tenant/provider can actually execute it

Operational readiness is deliberately **absent** from this model. Whether a WABA is verified or
public hosting exists is transient deployment state; `project-management/BLOCKERS.md` is its source
of truth, and execution stays gated by the existing connection/provider/live/security checks.

Rule Zero (CLAUDE.md §11.3): the L0 catalog carries no industry nouns. Vertical-specific commercial
capabilities are contributed declaratively by packs and read **by path**, the same sanctioned
pattern as `core/packs/taxonomy.py` — `core/` never imports `verticals/`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml

# repo_root/verticals — core/tenancy/capabilities.py → tenancy → core → repo root
_VERTICALS_ROOT = Path(__file__).resolve().parents[2] / "verticals"

Kind = Literal["feature", "agent", "channel", "channel_capability", "addon", "limit"]
Status = Literal["available", "beta", "partial", "planned"]
Visibility = Literal["public", "public_beta", "private_beta", "internal", "planned"]

# Maturity ordering — a capability may not depend on something less mature than itself.
_STATUS_RANK: dict[str, int] = {"planned": 0, "partial": 1, "beta": 2, "available": 3}

# Customer-perceivable capabilities only. Infrastructure is never a commercial capability.
_INFRA_TOKENS = (
    "rls", "outbox", "redis", "postgres", "migration", "langgraph", "worker", "scheduler",
    "queue", "database", "sandbox",
)


@dataclass(frozen=True)
class Capability:
    """One canonical, customer-perceivable capability.

    `runtime_grantable` means: **eligible to participate as an independent machine-authorization
    entitlement once the structured resolver supports it** (PLAN-2). It does *not* mean currently
    granted, globally enabled, provider connected, or deployment ready — `entitlements.py` decides
    what is effective today, and it is deliberately narrower than this flag.

    `evidence_refs` is **audit metadata**: auditable justification for a commercial-status claim,
    never authorization. A route string can drift or be wrong, so the test suite checks every
    route-shaped ref against the live OpenAPI paths. Real protection comes from the catalog
    invariants, the resolver, route/tool enforcement and the integration tests.
    """

    key: str
    label: str
    description: str
    category: str
    kind: Kind
    status: Status
    commercial_visibility: Visibility
    runtime_grantable: bool
    enforced_by: str | None = None
    evidence_refs: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    vertical: str | None = None  # None = L0 platform capability; slug when contributed by a pack


# ---- L0 catalog -------------------------------------------------------------------------------
# One entry per *authorization boundary or product surface* — never one per marketing bullet.
# Several public bullets may ride on a single capability (e.g. campaign analytics / ROI / growth
# analytics all resolve to `campaigns.analytics`); the bullet→capability mapping is PLAN-3/WEB work.

L0_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        key="conversations",
        label="Conversations",
        description="A shared inbox for customer chats, with drafted replies the owner approves.",
        category="engagement", kind="feature",
        status="available", commercial_visibility="public", runtime_grantable=True,
        evidence_refs=(
            "GET /v1/conversations",
            "GET /v1/conversations/{conversation_id}",
            "GET /v1/approvals",
            "POST /v1/approvals/{approval_id}/resolve",
        ),
    ),
    Capability(
        key="catalog",
        label="Catalog",
        description="Your product catalog with search and availability, used to ground replies.",
        category="operations", kind="feature",
        status="available", commercial_visibility="public", runtime_grantable=True,
        evidence_refs=("GET /v1/catalog/items", "GET /v1/catalog/search"),
    ),
    Capability(
        key="pricing",
        label="Pricing & quotes",
        description="Quote calculation with a recorded breakdown for every figure.",
        category="operations", kind="feature",
        status="available", commercial_visibility="public", runtime_grantable=False,
        enforced_by="rbac:catalog:read",
        evidence_refs=("POST /v1/pricing/compute",),
        depends_on=("catalog",),
    ),
    Capability(
        key="customers",
        label="Customers",
        description="Contacts, lead pipeline and a full activity timeline per customer.",
        category="engagement", kind="feature",
        status="available", commercial_visibility="public", runtime_grantable=True,
        evidence_refs=("GET /v1/customers", "GET /v1/customers/{contact_id}/timeline"),
    ),
    Capability(
        key="ghost_recovery",
        label="Ghost lead recovery",
        description="Finds leads that went silent, diagnoses why, and drafts a way back in.",
        category="engagement", kind="feature",
        status="available", commercial_visibility="public", runtime_grantable=True,
        evidence_refs=("GET /v1/leads", "POST /v1/leads/{lead_id}/recovery"),
        depends_on=("customers",),
    ),
    Capability(
        key="insights.business",
        label="Business performance insights",
        description="Week-over-week business outcomes plus spend, revenue and ROI transparency.",
        category="intelligence", kind="feature",
        status="available", commercial_visibility="public", runtime_grantable=False,
        enforced_by="rbac:insights:read",
        evidence_refs=("GET /v1/insights/summary", "GET /v1/insights/transparency"),
    ),
    Capability(
        key="agent.concierge",
        label="AI concierge",
        description="Answers customer questions from your catalog and prices, for your approval.",
        category="engagement", kind="agent",
        status="available", commercial_visibility="public", runtime_grantable=False,
        enforced_by="cp2b_agent_allowlist",
        evidence_refs=(
            "POST /webhooks/whatsapp",
            "GET /v1/ops/runs/{run_id}",
            "POST /v1/approvals/{approval_id}/resolve",
        ),
        depends_on=("conversations", "catalog"),
    ),
    Capability(
        key="channel.whatsapp",
        label="WhatsApp",
        description="Your WhatsApp business number connected as a conversation channel.",
        category="channels", kind="channel",
        status="available", commercial_visibility="public", runtime_grantable=False,
        enforced_by="channel_registry",
        evidence_refs=(
            "POST /v1/channels/whatsapp/connect",
            "GET /v1/channels/whatsapp/{channel_id}/health",
        ),
    ),
    Capability(
        key="campaigns.whatsapp",
        label="WhatsApp campaigns",
        description="Consent-based broadcasts to your customers, sent only after approval.",
        category="growth", kind="channel_capability",
        status="available", commercial_visibility="public", runtime_grantable=True,
        evidence_refs=("POST /v1/campaigns", "POST /v1/campaigns/{campaign_id}/send"),
        depends_on=("channel.whatsapp", "customers"),
    ),
    Capability(
        key="campaigns.analytics",
        label="Campaign analytics & attribution",
        description="Funnel, attribution and performance analysis for what you send.",
        category="intelligence", kind="feature",
        status="available", commercial_visibility="public", runtime_grantable=True,
        evidence_refs=("GET /v1/campaigns/{campaign_id}/analytics",),
        depends_on=("campaigns.whatsapp",),
    ),
    Capability(
        key="landing_pages",
        label="Landing pages",
        description=(
            "Generated campaign pages you approve, with lead capture and interest insights."
        ),
        category="growth", kind="feature",
        status="available", commercial_visibility="public", runtime_grantable=True,
        evidence_refs=(
            "POST /v1/landing/pages",
            "POST /v1/landing/pages/{page_id}/select",
            "GET /p/{page_id}",
            "POST /p/{page_id}/lead",
            "GET /v1/landing/pages/{page_id}/insights",
        ),
        depends_on=("catalog",),
    ),
    Capability(
        key="catalog.ingestion",
        label="Automated catalog ingestion",
        description="Bulk import and update your catalog from files, with review before loading.",
        category="operations", kind="feature",
        status="available", commercial_visibility="public", runtime_grantable=True,
        evidence_refs=(
            "POST /v1/imports",
            "POST /v1/imports/{batch_id}/load",
            "POST /v1/imports/{batch_id}/revert",
        ),
        depends_on=("catalog",),
    ),
    Capability(
        key="seats",
        label="Staff users",
        description="How many staff accounts the plan includes.",
        category="limits", kind="limit",
        status="available", commercial_visibility="public", runtime_grantable=False,
        enforced_by="cp3_seat_limit",
    ),
    # ---- Built but not customer-reachable end-to-end → never public --------------------------
    Capability(
        key="agent.nurture",
        label="Nurture agent",
        description="Internal: follow-up sequencing archetype.",
        category="engagement", kind="agent",
        status="partial", commercial_visibility="internal", runtime_grantable=False,
    ),
    Capability(
        key="agent.campaigner",
        label="Campaigner agent",
        description="Internal: campaign-authoring archetype; its execute tool is not wired.",
        category="growth", kind="agent",
        status="partial", commercial_visibility="internal", runtime_grantable=False,
    ),
    Capability(
        key="agent.ops",
        label="Operations agent",
        description="Internal: operations archetype; its tools are not wired.",
        category="operations", kind="agent",
        status="partial", commercial_visibility="internal", runtime_grantable=False,
    ),
    Capability(
        key="social.instagram_publishing",
        label="Instagram publishing",
        description="Internal: publishing adapter with no customer-reachable path.",
        category="growth", kind="channel_capability",
        status="partial", commercial_visibility="internal", runtime_grantable=False,
    ),
    Capability(
        key="ads.google",
        label="Google Ads",
        description="Internal: ads adapter with no customer-reachable path.",
        category="growth", kind="channel_capability",
        status="partial", commercial_visibility="internal", runtime_grantable=False,
    ),
    # ---- Not built → never sellable, never grantable ------------------------------------------
    Capability(
        key="seo",
        label="SEO",
        description="Planned: search/answer-engine visibility. Not built.",
        category="growth", kind="feature",
        status="planned", commercial_visibility="planned", runtime_grantable=False,
    ),
    Capability(
        key="agent.marketing",
        label="Dedicated marketing agent",
        description="Planned: a marketing agent. No such archetype exists.",
        category="growth", kind="agent",
        status="planned", commercial_visibility="planned", runtime_grantable=False,
    ),
    Capability(
        key="appointments",
        label="Appointment booking",
        description="Planned: store-visit booking. The calendar tool is not wired.",
        category="engagement", kind="feature",
        status="planned", commercial_visibility="planned", runtime_grantable=False,
    ),
    Capability(
        key="crm.automation",
        label="External CRM sync",
        description="Planned: reading and writing an external CRM. Those tools are not wired.",
        category="operations", kind="feature",
        status="planned", commercial_visibility="planned", runtime_grantable=False,
    ),
)

# Historical keys that must keep resolving. `ads.instagram` was misnamed: the implementation is
# publishing, not ads. The rest keep their spelling and are simply no longer grantable.
ALIASES: dict[str, str] = {"ads.instagram": "social.instagram_publishing"}


# ---- Vertical (L1) contributions ---------------------------------------------------------------


def _pack_capabilities(root: Path) -> tuple[Capability, ...]:
    """Read each pack's optional `commercial:` file. Keys are force-namespaced `<slug>.<key>` so a
    pack can never collide with, or silently shadow, an L0 capability."""
    from core.packs.contracts import CommercialPack  # local: keeps this module import-light

    out: list[Capability] = []
    if not root.is_dir():
        return ()
    for manifest_path in sorted(root.glob("*/pack.yaml")):
        manifest = yaml.safe_load(manifest_path.read_text()) or {}
        rel = manifest.get("commercial")
        if not rel:
            continue
        slug = str(manifest.get("pack") or manifest_path.parent.name)
        pack = CommercialPack.model_validate(
            yaml.safe_load((manifest_path.parent / rel).read_text())
        )
        for c in pack.capabilities:
            out.append(
                Capability(
                    key=f"{slug}.{c.key}", label=c.label, description=c.description,
                    category=c.category, kind=c.kind, status=c.status,
                    commercial_visibility=c.commercial_visibility,
                    runtime_grantable=c.runtime_grantable, enforced_by=c.enforced_by,
                    evidence_refs=tuple(c.evidence_refs), depends_on=tuple(c.depends_on),
                    vertical=slug,
                )
            )
    return tuple(out)


@lru_cache(maxsize=4)
def _catalog_cached(root: str) -> tuple[Capability, ...]:
    return L0_CAPABILITIES + _pack_capabilities(Path(root))


def catalog(*, root: Path | None = None) -> tuple[Capability, ...]:
    """The full canonical catalog: L0 platform capabilities plus every pack's contribution."""
    return _catalog_cached(str(root or _VERTICALS_ROOT))


def resolve_alias(key: str) -> str:
    """Map a historical key onto its canonical one. Unknown keys pass through unchanged."""
    return ALIASES.get(key, key)


def by_key(key: str, *, root: Path | None = None) -> Capability | None:
    for c in catalog(root=root):
        if c.key == resolve_alias(key):
            return c
    return None


def public_capabilities(*, root: Path | None = None) -> tuple[Capability, ...]:
    """Capabilities eligible for customer-facing presentation."""
    return tuple(
        c for c in catalog(root=root) if c.commercial_visibility in ("public", "public_beta")
    )


def grantable_keys(*, root: Path | None = None) -> frozenset[str]:
    """Keys **eligible** to become independent machine entitlements once PLAN-2's resolver lands.
    This is not the set that is effective today — see `entitlements.LEGACY_EFFECTIVE_KEYS`."""
    return frozenset(c.key for c in catalog(root=root) if c.runtime_grantable)


# ---- Invariants --------------------------------------------------------------------------------


@dataclass
class _Problems:
    items: list[str] = field(default_factory=list)

    def check(self, ok: bool, message: str) -> None:
        if not ok:
            self.items.append(message)


def validate_catalog(caps: tuple[Capability, ...]) -> list[str]:
    """Return every invariant violation. Empty list = a well-formed catalog.

    These invariants — not the presence of an evidence string — are what stop a not-built or
    not-reachable capability from being sold or authorized.
    """
    p = _Problems()
    keys = [c.key for c in caps]
    p.check(len(keys) == len(set(keys)), f"duplicate capability keys: {sorted(keys)}")
    index = {c.key: c for c in caps}

    for c in caps:
        w = f"{c.key}:"
        if c.status == "planned":
            p.check(not c.runtime_grantable, f"{w} planned but runtime_grantable")
            p.check(c.commercial_visibility == "planned", f"{w} planned but visibility is not")
        if c.status == "partial":
            p.check(
                c.commercial_visibility in ("internal", "private_beta"),
                f"{w} partial capabilities may never be publicly presented",
            )
        if c.commercial_visibility in ("public", "public_beta"):
            p.check(c.status in ("available", "beta"), f"{w} public but status={c.status}")
            p.check(bool(c.evidence_refs) or c.kind == "limit", f"{w} public without evidence_refs")
        if c.commercial_visibility == "public_beta":
            p.check(c.status == "beta", f"{w} public_beta requires status=beta")
        if c.status == "available" and not c.runtime_grantable:
            p.check(
                c.enforced_by is not None,
                f"{w} available and not an authorization boundary → name what governs it",
            )
        if c.vertical is not None:
            p.check(c.key.startswith(f"{c.vertical}."), f"{w} pack key must be <slug>.<key>")
        p.check(
            not any(t in c.key.lower() for t in _INFRA_TOKENS),
            f"{w} infrastructure is never a commercial capability",
        )
        for dep in c.depends_on:
            p.check(dep in index, f"{w} depends on unknown capability {dep!r}")
            if dep in index:
                p.check(
                    _STATUS_RANK[index[dep].status] >= _STATUS_RANK[c.status],
                    f"{w} depends on less-mature {dep!r}",
                )
            p.check(dep != c.key, f"{w} depends on itself")

    for legacy, canonical in ALIASES.items():
        p.check(canonical in index, f"alias {legacy!r} points at unknown {canonical!r}")
        p.check(legacy not in index, f"alias {legacy!r} also exists as a real key")

    return p.items
