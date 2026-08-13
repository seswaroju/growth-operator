# Current Task

This file always describes exactly one active ticket. When a ticket completes, append its verified summary to
`IMPLEMENTATION_LOG.md` and mark this task as
`Completed — awaiting founder review`.

Do not replace this file with a new ticket until the founder explicitly
selects and approves the next ticket.

---

## PLAN-4 · Operator Plan Builder — **Completed — awaiting founder review** (2026-08-13)

Branch `feature/plan-4-plan-builder`. **Migration 051** (SECURITY DEFINER only).

- **STOP-and-flag resolved:** a sold *custom* plan could be rewritten in place — price, entitlements
  and seats — for a store with an active subscriber. `SoldPlanImmutable` now locks everything except
  `active` once **any** subscription of **any** status has referenced a plan.
- `plan_has_subscription_history(uuid)` (migration 051) — SECURITY DEFINER, boolean-only, fixed
  `search_path`, REVOKE FROM PUBLIC, EXECUTE to `app_rw`. Requests get the *fact* without the rows;
  no BYPASSRLS credential enters the API.
- **Serialization:** every plan mutation and `assign_subscription()` take `SELECT … FOR UPDATE` on
  the plan row **before** deciding, so a subscriber can never be created and then have the terms
  rewritten.
- **`active=false` now actually blocks assignment**, and the target is validated *before* the
  current subscription is cancelled — a rejected assignment never leaves a store plan-less.
- `core/billing/plan_builder.py` — catalog-driven selection (registry **and** catalog must agree),
  vertical scoping, dependency block-save with explicit hints, promotion validation, and a
  deterministic preview.
- `compose()` / `ResolutionContext` extracted from PLAN-2's `resolve()` — **behaviour-preserving**;
  preview runs the resolver's own composition with a *declared* context, so no fake tenant or
  throwaway subscription is created.
- `config.vertical` now persisted; **`PRESET_VERSION` 1 → 2**; old canonical snapshots recover their
  vertical by exact `preset_key` lookup, never string parsing.

**PLAN-5 still owns runtime enforcement** — `/v1/imports`, `/v1/rates`, `campaigns.analytics`, and
the **P0** plan-reassignment agent reconciliation (#30).

**Do not start PLAN-5 automatically — the founder selects the next ticket.**
