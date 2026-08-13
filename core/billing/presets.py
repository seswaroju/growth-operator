"""Canonical commercial presets — Recover / Grow / Scale (PLAN-3).

These compose two existing truths rather than inventing a third: the PLAN-1 capability catalog says
what capabilities exist and how mature they are, and the PLAN-2 structured contract says how a plan
expresses machine authorization. PLAN-3 only decides **what we sell**.

Definitions live here in code; `billing_plans` rows are **materialised snapshots** of them. Both are
needed — provisioning takes a `plan_id` foreign key, a future plan builder copies rows, and a
historical subscription must keep resolving against the row it was actually sold.

Three separations are load-bearing:

*Machine config is not marketing copy.* `config.entitlements` holds canonical keys only; the public
bullets live under `config.display` and can never authorize anything. `billing_plans.features` is
written **empty** — it is legacy display data and must never become machine authority again.

*A capability that is not its own authorization boundary is not an entitlement.* The concierge is a
plan **agent** (CP-2b enforces it), WhatsApp is a plan **channel**, team seats are the CP-3 columns,
and pricing/business-insights are governed by role permissions. None of them appear in
`config.entitlements`, because inventing a capability key for them would create a second gate for
something already governed.

*Commercial tier placement is a business decision, not a property of a capability.* A vertical pack
therefore declares tier placement explicitly in `commercial/plan_presets.yaml`; we never infer that
"public and grantable" implies the top tier. Rule Zero holds: this module contains no vertical noun,
imports nothing from `verticals/`, and reads pack overlays by path (the sanctioned pattern used by
`core/packs/taxonomy.py` and the PLAN-1 capability loader).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from core.tenancy.capabilities import by_key
from core.tenancy.entitlements import dependency_satisfied

# repo_root/verticals — core/billing/presets.py → billing → core → repo root
_VERTICALS_ROOT = Path(__file__).resolve().parents[2] / "verticals"

#: Bumped only when a canonical definition changes. A row already referenced by a subscription is
#: never rewritten regardless — see `plan_is_sold`.
#: v2 (PLAN-4) persists `config.vertical`, so a plan's vertical is read from data rather than
#: inferred by parsing a name or splitting a preset key.
PRESET_VERSION = 2

#: The generic tiers a vertical overlay may extend.
TIERS: tuple[str, ...] = ("recover", "grow", "scale")


@dataclass(frozen=True)
class Preset:
    """One sellable plan. `entitlements` are canonical machine keys; `display_bullets` are the
    customer-facing benefits, several of which may ride on a single entitlement."""

    preset_key: str
    name: str
    description: str
    price_minor: int
    entitlements: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    max_managers: int = 0
    max_staff: int = 0
    recommended: bool = False
    display_bullets: tuple[str, ...] = ()
    vertical: str | None = None

    @property
    def team_seats(self) -> int:
        """Capped manager + staff seats. The owner is never counted, and read-only viewers are
        uncapped by CP-3 — display copy must not imply viewers consume these."""
        return self.max_managers + self.max_staff

    def to_config(self) -> dict[str, Any]:
        """The PLAN-2 structured contract plus canonical identity and display metadata.

        `preset_key` / `preset_version` / `display` ride along as `PlanConfig` extras — that model
        is `extra="allow"` precisely so metadata survives a round trip without becoming authority.
        """
        return {
            "entitlement_schema_version": 1,
            "entitlements": list(self.entitlements),
            "vertical": self.vertical,
            "agents": list(self.agents),
            "channels": list(self.channels),
            "addons": [],
            "promotions": [],
            "preset_key": self.preset_key,
            "preset_version": PRESET_VERSION,
            "display": {
                "bullets": list(self.display_bullets),
                "recommended": self.recommended,
                "team_seats": self.team_seats,
            },
        }


# ---- The generic tiers -------------------------------------------------------------------------
# Prices are the founder-approved targets. Meta messaging fees and advertising spend are billed
# separately and are deliberately not bundled here.

RECOVER = Preset(
    preset_key="recover",
    name="Recover",
    description="Stop losing the leads you already have.",
    price_minor=399_900,
    entitlements=("conversations", "catalog", "customers", "ghost_recovery"),
    agents=("concierge",),
    channels=("whatsapp",),
    max_managers=0, max_staff=2,
    display_bullets=(
        "Ghost Lead Recovery",
        "AI Concierge / Priya",
        "Customer CRM & lifecycle",
        "Catalog search & availability",
        "Pricing & quote assistance",
        "Business Performance Insights",
        "WhatsApp",
        "Team seats: 2",
    ),
)

GROW = Preset(
    preset_key="grow",
    name="Grow",
    description="Generate and convert more demand.",
    price_minor=699_900,
    entitlements=(
        *RECOVER.entitlements, "campaigns.whatsapp", "campaigns.analytics", "landing_pages"),
    agents=RECOVER.agents,
    channels=RECOVER.channels,
    max_managers=1, max_staff=4,
    recommended=True,
    display_bullets=(
        "Everything in Recover, plus:",
        "Consent-based WhatsApp campaigns",
        "Campaign analytics & attribution",
        "Campaign ROI / performance analysis",
        "Landing-page generation",
        "Landing lead capture",
        "Landing-page / product-interest insights",
        "Growth analytics",
        "Team seats: 5",
    ),
)

SCALE = Preset(
    preset_key="scale",
    name="Scale",
    description="Run more of growth and operations with AI.",
    price_minor=1_299_900,
    entitlements=(*GROW.entitlements, "catalog.ingestion"),
    agents=GROW.agents,
    channels=GROW.channels,
    max_managers=2, max_staff=8,
    display_bullets=(
        "Everything in Grow, plus:",
        "Automated catalog ingestion & updates",
        "Team seats: 10",
    ),
)

GENERIC_PRESETS: tuple[Preset, ...] = (RECOVER, GROW, SCALE)
_BY_TIER: dict[str, Preset] = {p.preset_key: p for p in GENERIC_PRESETS}


# ---- Vertical overlays -------------------------------------------------------------------------


def overlay_path(slug: str, *, root: Path | None = None) -> Path:
    return (root or _VERTICALS_ROOT) / slug / "commercial" / "plan_presets.yaml"


@lru_cache(maxsize=4)
def _compose_cached(root: str) -> tuple[Preset, ...]:
    from core.packs.contracts import VerticalPresetOverlay

    out: list[Preset] = list(GENERIC_PRESETS)
    base = Path(root)
    if not base.is_dir():
        return tuple(out)
    for manifest in sorted(base.glob("*/pack.yaml")):
        data = yaml.safe_load(manifest.read_text()) or {}
        slug = str(data.get("pack") or manifest.parent.name)
        path = overlay_path(slug, root=base)
        if not path.is_file():
            continue  # a pack contributing no commercial overlay is normal
        overlay = VerticalPresetOverlay.model_validate(yaml.safe_load(path.read_text()) or {})
        for tier, addition in sorted(overlay.tiers.items()):
            generic = _BY_TIER.get(tier)
            if generic is None:
                continue  # reported by `validate_presets`, not silently applied
            out.append(
                Preset(
                    preset_key=f"{tier}.{slug}",
                    # The slug is data; no vertical noun is written into this module.
                    name=f"{generic.name} · {slug.replace('_', ' ').title()}",
                    description=generic.description,
                    price_minor=generic.price_minor,
                    entitlements=(*generic.entitlements, *addition.entitlements),
                    agents=generic.agents,
                    channels=generic.channels,
                    max_managers=generic.max_managers,
                    max_staff=generic.max_staff,
                    recommended=generic.recommended,
                    display_bullets=(*generic.display_bullets, *addition.display_bullets),
                    vertical=slug,
                )
            )
    return tuple(out)


def all_presets(*, root: Path | None = None) -> tuple[Preset, ...]:
    """The generic tiers plus one variant per pack tier overlay."""
    return _compose_cached(str(root or _VERTICALS_ROOT))


# ---- Static validation -------------------------------------------------------------------------


@dataclass
class _Problems:
    items: list[str] = field(default_factory=list)

    def check(self, ok: bool, message: str) -> None:
        if not ok:
            self.items.append(message)


def validate_overlays(*, root: Path | None = None) -> list[str]:
    """Validate every pack overlay **statically** — no tenant, no database.

    A vertical may only place its *own* capabilities, and only ones that are genuinely sellable."""
    from core.packs.contracts import VerticalPresetOverlay

    p = _Problems()
    base = root or _VERTICALS_ROOT
    if not base.is_dir():
        return []
    for manifest in sorted(base.glob("*/pack.yaml")):
        data = yaml.safe_load(manifest.read_text()) or {}
        slug = str(data.get("pack") or manifest.parent.name)
        path = overlay_path(slug, root=base)
        if not path.is_file():
            continue
        try:
            overlay = VerticalPresetOverlay.model_validate(yaml.safe_load(path.read_text()) or {})
        except Exception as exc:  # noqa: BLE001 — reported, never raised into a seed run
            p.items.append(f"{slug}: unparseable overlay ({type(exc).__name__})")
            continue
        for tier, addition in sorted(overlay.tiers.items()):
            w = f"{slug}.{tier}:"
            p.check(tier in TIERS, f"{w} unknown tier (expected one of {TIERS})")
            for key in addition.entitlements:
                cap = by_key(key)
                p.check(cap is not None, f"{w} {key!r} is not in the canonical catalog")
                if cap is None:
                    continue
                p.check(cap.vertical == slug,
                        f"{w} {key!r} belongs to {cap.vertical!r}, not this pack")
                p.check(cap.runtime_grantable, f"{w} {key!r} is not an authorization boundary")
                p.check(cap.status in ("available", "beta"), f"{w} {key!r} status={cap.status}")
                p.check(cap.commercial_visibility in ("public", "public_beta"),
                        f"{w} {key!r} is not commercially presentable")
    return p.items


def validate_presets(presets: tuple[Preset, ...] | None = None) -> list[str]:
    """Every composed preset must be internally coherent and structurally resolvable.

    The dependency check reuses the **resolver's own rule** rather than reimplementing it, so a
    preset cannot be declared valid here and then be rejected at runtime by PLAN-2."""
    p = _Problems()
    presets = presets if presets is not None else all_presets()

    keys = [x.preset_key for x in presets]
    p.check(len(keys) == len(set(keys)), f"duplicate preset keys: {sorted(keys)}")
    names = [x.name for x in presets]
    p.check(len(names) == len(set(names)), f"duplicate plan names: {sorted(names)}")

    for preset in presets:
        w = f"{preset.preset_key}:"
        caps = set(preset.entitlements)
        chans = set(preset.channels)
        agents = set(preset.agents)
        p.check(len(preset.entitlements) == len(caps), f"{w} duplicate entitlements")
        p.check(preset.price_minor > 0, f"{w} price must be positive")
        p.check(preset.max_managers >= 0 and preset.max_staff >= 0, f"{w} negative seat limit")

        for key in preset.entitlements:
            cap = by_key(key)
            p.check(cap is not None, f"{w} {key!r} is not in the canonical catalog")
            if cap is None:
                continue
            p.check(cap.runtime_grantable,
                    f"{w} {key!r} is not an authorization boundary — use agents/channels/limits")
            p.check(cap.status in ("available", "beta"), f"{w} {key!r} status={cap.status}")
            p.check(cap.commercial_visibility in ("public", "public_beta"),
                    f"{w} {key!r} is not commercially presentable")
            for dep in cap.depends_on:
                ok, reason = dependency_satisfied(dep, caps, chans, agents)
                p.check(ok, f"{w} {key!r} unsatisfiable dependency — {reason}")

        for slug in preset.agents:
            cap = by_key(f"agent.{slug}")
            p.check(cap is not None, f"{w} unknown agent {slug!r}")
            if cap is not None:
                p.check(cap.commercial_visibility in ("public", "public_beta"),
                        f"{w} agent {slug!r} is not sellable ({cap.commercial_visibility})")

        for ch in preset.channels:
            cap = by_key(f"channel.{ch}")
            p.check(cap is not None, f"{w} unknown channel {ch!r}")
            if cap is not None:
                # Registry existence is technical; the catalog decides commercial truth.
                p.check(cap.commercial_visibility in ("public", "public_beta"),
                        f"{w} channel {ch!r} is not sellable ({cap.commercial_visibility})")

    return p.items


# ---- Materialisation ---------------------------------------------------------------------------

#: SQL is shared by the CLI and the tests so both prove the same behaviour.
FIND_SQL = ("SELECT id, name, price_minor, active, description, features, max_managers, "
            "max_staff, config FROM billing_plans WHERE config->>'preset_key' = :k")
#: One definition of "sold", shared with the request path. The SECURITY DEFINER function (051) is
#: the only place this question is answered, so an RLS-scoped session cannot get a false negative.
SOLD_SQL = "SELECT public.plan_has_subscription_history(CAST(:p AS uuid))"
PRIVILEGE_SQL = ("SELECT COALESCE(bool_or(rolbypassrls OR rolsuper), false) FROM pg_roles "
                 "WHERE rolname = current_user")
INSERT_SQL = ("INSERT INTO billing_plans (name, price_minor, active, description, features, "
              "max_managers, max_staff, config) VALUES (:n, :p, true, :d, '[]'::jsonb, :mm, :ms, "
              "CAST(:cfg AS jsonb)) RETURNING id")
UPDATE_SQL = ("UPDATE billing_plans SET name = :n, price_minor = :p, description = :d, "
              "features = '[]'::jsonb, max_managers = :mm, max_staff = :ms, "
              "config = CAST(:cfg AS jsonb) WHERE id = :id")


@dataclass(frozen=True)
class SeedOutcome:
    preset_key: str
    action: str  # created | updated | unchanged | skipped_sold | skipped_drift | error
    plan_id: str | None = None
    detail: str = ""


def _row_matches(row: Any, preset: Preset) -> bool:
    stored = row["config"]
    if isinstance(stored, str):
        stored = json.loads(stored)
    features = row["features"]
    if isinstance(features, str):
        features = json.loads(features)
    return bool(
        row["name"] == preset.name
        and row["price_minor"] == preset.price_minor
        and row["description"] == preset.description
        and row["max_managers"] == preset.max_managers
        and row["max_staff"] == preset.max_staff
        and features == []
        and stored == preset.to_config()
    )


class InsufficientVisibility(RuntimeError):
    """The connection could RLS-mask `billing_subscriptions`, so its sold/unsold answer is not
    trustworthy. Refusing is mandatory: as an ordinary RLS-bound role the existence check returns
    false for **every** plan, and the seeder would then happily rewrite a plan someone bought."""


async def assert_global_visibility(session: Any) -> None:
    """Require an effective privilege, not a role name — `rolbypassrls` or superuser."""
    from sqlalchemy import text

    ok = (await session.execute(text(PRIVILEGE_SQL))).scalar_one()
    if not ok:
        raise InsufficientVisibility(
            "seeding requires a connection that can read billing_subscriptions across all tenants "
            "(BYPASSRLS or superuser); refusing to decide sold/unsold behind row-level security")


async def plan_is_sold(session: Any, plan_id: Any) -> bool:
    """Whether **any** subscription of **any** status has ever referenced this plan.

    Cancelled history counts: once a row has been sold it is historical commercial truth, and what
    a past subscriber bought must stay readable exactly as it was."""
    from sqlalchemy import text

    return bool((await session.execute(text(SOLD_SQL), {"p": str(plan_id)})).scalar_one())


async def apply_presets(
    session: Any, *, presets: tuple[Preset, ...] | None = None, dry_run: bool = False
) -> list[SeedOutcome]:
    """Idempotently materialise the canonical presets. Requires global visibility.

    Identity is `config.preset_key`, so a row without one — every legacy plan and every operator or
    plan-builder creation — is invisible to this function and can never be touched by it.
    """
    from sqlalchemy import text

    await assert_global_visibility(session)
    presets = presets if presets is not None else all_presets()

    problems = validate_overlays() + validate_presets(presets)
    if problems:
        return [SeedOutcome("*", "error", None, "; ".join(problems))]

    outcomes: list[SeedOutcome] = []
    for preset in presets:
        cfg = json.dumps(preset.to_config())
        args = {"n": preset.name, "p": preset.price_minor, "d": preset.description,
                "mm": preset.max_managers, "ms": preset.max_staff, "cfg": cfg}
        row = (await session.execute(text(FIND_SQL), {"k": preset.preset_key})).mappings().first()

        if row is None:
            if dry_run:
                outcomes.append(SeedOutcome(preset.preset_key, "created", None, "(dry run)"))
                continue
            try:
                new_id = (await session.execute(text(INSERT_SQL), args)).scalar_one()
            except Exception as exc:  # noqa: BLE001 — e.g. the UNIQUE name already belongs to a row
                outcomes.append(
                    SeedOutcome(preset.preset_key, "error", None, type(exc).__name__))
                continue
            outcomes.append(SeedOutcome(preset.preset_key, "created", str(new_id)))
            continue

        pid = str(row["id"])
        if await plan_is_sold(session, row["id"]):
            # No override exists, by design: a sold plan is a commercial snapshot. A new commercial
            # version must be materialised as a new row rather than rewriting what was purchased.
            outcomes.append(SeedOutcome(
                preset.preset_key, "skipped_sold", pid, "referenced by a subscription"))
            continue

        stored = row["config"]
        if isinstance(stored, str):
            stored = json.loads(stored)
        stored_version = stored.get("preset_version")

        if _row_matches(row, preset):
            outcomes.append(SeedOutcome(preset.preset_key, "unchanged", pid))
        elif isinstance(stored_version, int) and stored_version < PRESET_VERSION:
            if not dry_run:
                await session.execute(text(UPDATE_SQL), {**args, "id": row["id"]})
            outcomes.append(SeedOutcome(preset.preset_key, "updated", pid))
        else:
            # Same version but different content — somebody edited it. Report, never clobber.
            outcomes.append(SeedOutcome(
                preset.preset_key, "skipped_drift", pid,
                f"row differs from code at preset_version={stored_version}"))
    return outcomes
