# Current Task

This file always describes exactly one active ticket. When a ticket completes, append its verified summary to
`IMPLEMENTATION_LOG.md` and mark this task as
`Completed — awaiting founder review`.

Do not replace this file with a new ticket until the founder explicitly
selects and approves the next ticket.

---

## PLAN-2 · Structured entitlement resolver — **Completed — awaiting founder review** (2026-08-13)

Branch `feature/plan-2-entitlement-resolver`. **No migration.**

- `core/tenancy/plan_config.py` (NEW) — typed `billing_plans.config`: `entitlement_schema_version`
  (the explicit legacy/structured boundary), `entitlements`, `agents`, `channels`, `addons`,
  `promotions`. Machine authorization moved off free-text `billing_plans.features`.
- `core/tenancy/entitlements.py` — `resolve()` returns `EffectiveEntitlements` (capabilities,
  agents, channels, limits, grants, excluded, subscription_state, plan). `entitlements()` remains a
  thin `frozenset[str]` wrapper, so the four `requires_feature` gates and `/v1/orgs/me` are
  untouched.
- **No active subscription → zero paid capabilities.** Public `BASELINE_FEATURES` /
  `GRANTABLE_FEATURES` / `ALL_FEATURES` deleted; the historical set survives only as the private
  `_LEGACY_ENT1A_BASELINE` inside the compatibility loader.
- **Active legacy plan** reconstructs ENT-1a semantics: historical baseline ∪ valid legacy features,
  plus the channels those capabilities necessarily imply (derived from PLAN-1 `depends_on`, not
  hardcoded). Provenance `legacy_compat`, never `plan`.
- Component-aware dependencies · pack-aware vertical filtering · tenant/pack-aware agent validation
  (binding existence, **not** instance status) · absolute UTC promotion windows.

**PLAN-2 resolves; it does not enforce.** `catalog.ingestion`, `campaigns.analytics` and
`jewelry.rate_operations` are computed but still ungated on their routes — PLAN-5 owns that,
including the **P0** plan-reassignment agent reconciliation.

**Do not start PLAN-3 automatically — the founder selects the next ticket.**
