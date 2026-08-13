"""Typed plan configuration — the structured commercial contract on `billing_plans.config` (PLAN-2).

`billing_plans.features` predates the canonical capability catalog and accepts free-form
`list[str]`, so it is display/marketing data that must never become permanent machine authority.
PLAN-2 moves machine authorization into `billing_plans.config` (existing JSONB, **no migration**):

    {
      "entitlement_schema_version": 1,
      "entitlements": ["ghost_recovery", "campaigns.whatsapp"],
      "agents": ["concierge"],
      "channels": ["whatsapp"],
      "addons": [],
      "promotions": [...]
    }

**Mode is decided only by `entitlement_schema_version`** — never by `entitlements is None`. A typo
such as `"entitlments": []` is preserved verbatim by `extra="allow"`, carries zero authorization
meaning, and cannot flip a structured plan back onto the legacy path.

    absent   → legacy plan; `billing_plans.features` is the compatibility input
    1        → structured plan; `config.entitlements` is authoritative, `features` is ignored
    anything else → fail closed; we refuse to guess a future schema

Promotions are **absolute calendar windows**, not per-subscriber durations: a window running
1–31 March grants nothing to a tenant who subscribes in April. "30 days from each tenant's
subscription start" is a different, subscription-relative semantic that is deliberately not
implemented and not stubbed for.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

#: The only structured schema this resolver understands.
CURRENT_SCHEMA_VERSION = 1


class PromotionDef(BaseModel):
    """A time-boxed capability grant attached to a plan.

    Strict: an unknown key is a typo, and a typo in a promotion must not silently widen access.
    A malformed entry is dropped by the resolver with an explicit exclusion rather than raising —
    one bad promotion must not deny every request for that tenant.
    """

    model_config = ConfigDict(extra="forbid")

    capability_key: str
    label: str | None = None
    enabled: bool = True
    starts_at: datetime
    ends_at: datetime | None = None

    @field_validator("starts_at", "ends_at")
    @classmethod
    def _must_be_tz_aware(cls, v: datetime | None) -> datetime | None:
        """Reject naive timestamps instead of assuming UTC — guessing a timezone would silently
        shift a promotion window by hours."""
        if v is None:
            return None
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("promotion timestamps must be timezone-aware")
        return v.astimezone(UTC)

    def active_at(self, now: datetime) -> bool:
        """Start **inclusive**, end **exclusive**, UTC. Read-time — no cron is required for
        correctness, and an expired promotion may remain stored as history while granting nothing.
        """
        if not self.enabled:
            return False
        if now < self.starts_at:
            return False
        return self.ends_at is None or now < self.ends_at


class PlanConfig(BaseModel):
    """`billing_plans.config`, typed.

    Open on purpose: operators have been able to write arbitrary keys since CP-1, and round-tripping
    a plan through a future plan builder must not silently delete data the schema does not yet know
    about. Unknown keys are preserved and carry **zero** authorization meaning.
    """

    model_config = ConfigDict(extra="allow")

    entitlement_schema_version: int | None = None
    entitlements: list[str] | None = None
    # PLAN-4: which vertical this plan is authored for (`None` = generic). Authoring metadata that
    # constrains which capabilities may be *selected*; it is not a resolution input — the runtime
    # pack filter keys off the capability's own `vertical` and remains the backstop.
    vertical: str | None = None
    agents: list[str] = []
    channels: list[str] = []
    addons: list[str] = []
    # Deliberately raw: parsed per-entry by `parse_promotions` so ONE malformed promotion is
    # excluded with a reason instead of failing the whole config and denying the tenant.
    promotions: list[Any] = []

    @property
    def is_structured(self) -> bool:
        return self.entitlement_schema_version is not None

    @property
    def is_known_schema(self) -> bool:
        return self.entitlement_schema_version == CURRENT_SCHEMA_VERSION


def parse_plan_config(raw: object) -> PlanConfig:
    """Parse a stored `config` value, tolerating anything a legacy row may hold.

    A config that will not parse at all yields an **empty legacy** config: the plan then grants only
    what the legacy compatibility path allows, which is the safe direction. A structured plan whose
    body is malformed is caught by the resolver, which fails it closed."""
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except ValueError:
            return PlanConfig()
    if not isinstance(raw, dict):
        return PlanConfig()
    try:
        return PlanConfig.model_validate(raw)
    except Exception:  # noqa: BLE001 — a malformed config must not deny the whole tenant
        # Retain the version marker if it is readable, so a structured plan with a broken body is
        # still recognised as structured and fails closed instead of silently going legacy.
        version = raw.get("entitlement_schema_version")
        return PlanConfig(
            entitlement_schema_version=version if isinstance(version, int) else None,
            entitlements=None,
        )


def parse_promotions(raw: object) -> tuple[list[PromotionDef], list[str]]:
    """Parse promotions one at a time, returning `(valid, errors)`.

    Per-entry parsing is deliberate: one malformed promotion must not invalidate the others, and
    each failure is surfaced to the operator rather than swallowed."""
    if not isinstance(raw, list):
        return [], []
    valid: list[PromotionDef] = []
    errors: list[str] = []
    for i, item in enumerate(raw):
        try:
            valid.append(PromotionDef.model_validate(item))
        except Exception as exc:  # noqa: BLE001 — surfaced as an exclusion, never raised
            key = item.get("capability_key") if isinstance(item, dict) else None
            errors.append(f"{key or f'promotion[{i}]'}: {type(exc).__name__}")
    return valid, errors
