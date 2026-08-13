# Current Task

This file always describes exactly one active ticket. When a ticket completes, append its verified summary to
`IMPLEMENTATION_LOG.md` and mark this task as
`Completed — awaiting founder review`.

Do not replace this file with a new ticket until the founder explicitly
selects and approves the next ticket.

---

## PLAN-5 · Runtime enforcement + agent reconciliation — **Completed — awaiting founder review** (2026-08-13)

Branch `feature/plan-5-runtime-enforcement`. **Migration 051 only** (the PLAN-4 SECDEF; PLAN-5 adds
no schema).

**Before this ticket the entire enforcement surface was four `requires_feature` gates.** The audit
found three gaps beyond the three known ones: **mediation had no entitlement check at all** (an agent
could `landing_page.publish` for a store that never bought landing pages), `recovery_sweep` processed
**every** organization, and `campaign_fanout` kept sending after a downgrade.

- `core/tenancy/entitlements.py` — `assert_entitled`, `is_entitled`, `assert_vertical_entitled`,
  `assert_agent_executable`.
- `core/tenancy/enforcement.py` (NEW) — per-surface inventory; every sellable capability's surfaces
  are enforced-with-a-named-test or explicitly exempt. `missing`/`unknown` are not expressible.
- `core/mediation/{proxy,tools}.py` — every tool declares `TOOL_CAPABILITY` or
  `TOOL_PLAN_EXEMPT`; the proxy re-checks **agent authority and tool capability** before execute.
- `core/runtime/executor.py` — `_drive()` is the authoritative boundary, so `start_run`,
  `resume_run` and `resume_after_approval` all converge on it; a downgraded run ends `interrupted`.
- Landing public runtime (`published_spec`, `record_public_event`, `capture_lead`) gated **inside
  the services**, denying neutrally — 404/204/no-capture, never disclosing billing state.
- `reconcile_plan_agents` **never rewrites operational status**; a manual pause survives a
  downgrade→upgrade cycle. Commercial authority is evaluated at execution time instead.
- Jobs classified: `recovery_sweep` and `campaign_fanout` gated (the latter halts once with
  `halt_reason="entitlement_revoked"`); the import reaper and rate-provider ingestion stay exempt as
  cleanup/platform infrastructure.

**Historical data continuity:** a cancelled store still reads conversations, customers, catalog,
leads, campaigns, landing pages, imports, approvals and rate freshness — and can execute nothing.

**Do not start the next ticket automatically — the founder selects it.**
