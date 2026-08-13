# Current Task

This file always describes exactly one active ticket. When a ticket completes, append its verified summary to
`IMPLEMENTATION_LOG.md` and mark this task as
`Completed — awaiting founder review`.

Do not replace this file with a new ticket until the founder explicitly
selects and approves the next ticket.

---

## PLAN-3 · Recover/Grow/Scale structured presets — **Completed — awaiting founder review** (2026-08-13)

Branch `feature/plan-3-plan-presets`. **No migration.**

- `core/billing/presets.py` (NEW) — canonical definitions (Recover ₹3,999 / Grow ₹6,999 *recommended*
  / Scale ₹12,999), explicit vertical overlay composition, static validation, idempotent
  materialisation. **Rule Zero verified** — no vertical noun in the module.
- `verticals/jewelry/commercial/plan_presets.yaml` (NEW) — declares tier placement **explicitly**
  (`scale: [jewelry.rate_operations]`). Placement is never inferred from public+grantable.
- `core/billing/service.py` — `CanonicalPresetLocked`: the legacy CP-1 editor can no longer edit a
  canonical preset (it rebuilds `config` from agents/channels/addons and would strip the structured
  contract); `create_plan` refuses caller-supplied `preset_key`. API → **409**.
- `scripts/seed_plans.py` (NEW) — asserts **effective** global visibility (`rolbypassrls OR
  rolsuper`), never a role name, before deciding sold/unsold.
- Canonical rows are **immutable once referenced by any subscription** (active *or* cancelled).
  No override flag exists.
- `billing_plans.features = []` on every preset; bullets live in `config.display`.

**Seeded (dev):** `recover` 3bfd21ee · `grow` 8251fa31 · `scale` 2151aa7b · `scale.jewelry` 06fb735a.
25 legacy/custom rows untouched.

**PLAN-3 defines what tiers should grant; it does not enforce.** PLAN-5 still owns `/v1/imports`,
`/v1/rates`, `campaigns.analytics` gating and the **P0** plan-reassignment agent reconciliation.

**Do not start PLAN-4 automatically — the founder selects the next ticket.**
