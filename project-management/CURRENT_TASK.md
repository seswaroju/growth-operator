# Current Task

This file always describes exactly one active ticket. When a ticket completes, append its verified summary to
`IMPLEMENTATION_LOG.md` and mark this task as
`Completed — awaiting founder review`.

Do not replace this file with a new ticket until the founder explicitly
selects and approves the next ticket.

---

## PLAN-1 · Canonical capability catalog + vocabulary — **Completed — awaiting founder review** (2026-08-13)

Branch `feature/plan-1-capability-catalog`. **A vocabulary ticket, by explicit founder ruling: it
builds the canonical catalog and tightens four unsafe legacy keys. It does not widen what any tenant
can do.**

- `core/tenancy/capabilities.py` (NEW) — the canonical, **global, org-independent** product
  vocabulary. `Capability(key, label, description, category, kind, status, commercial_visibility,
  runtime_grantable, enforced_by, evidence_refs, depends_on, vertical)` + `validate_catalog()`.
- `core/tenancy/entitlements.py` — now **resolution only**. `LEGACY_EFFECTIVE_KEYS` is a frozen
  compatibility shim = the ENT-1a set **minus** `seo`, `agent.marketing`, `ads.instagram`,
  `ads.google`. `normalize()` consults **only** that shim, never `runtime_grantable`.
- L1 contribution: optional `commercial:` manifest key → `verticals/jewelry/commercial/
  capabilities.yaml` (`jewelry.rate_operations`). Loaded into the global catalog, **not** effective
  for any tenant — PLAN-2 owns pack filtering.
- **No migration.** **Legacy-key audit: 0 affected plan rows** (all 11 plans hold `[]` or
  `['campaigns.whatsapp']`), 0 active subscriptions.

**Canonical ticket sequence (founder-ratified 2026-08-13, frozen — identical in the design review
Part 7, `BACKLOG.md` and `IMPLEMENTATION_LOG.md`):** PLAN-1 catalog · PLAN-2 structured resolver
(provenance, subscription-state, pack filtering, promotion evaluation, compatibility loader) ·
PLAN-3 presets (snapshot, no live inheritance) · PLAN-4 Plan Builder UI incl. promotion authoring ·
PLAN-5 enforcement extension + ungated inventory.

**Do not start PLAN-2 automatically — the founder selects the next ticket.**
