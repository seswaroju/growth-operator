# Operator Console v2 + Tenant 360 — ticket backlog

Founder feedback (2026-08-10) after the UX pass, ticketed for one-at-a-time delivery. `docs/` is the
read-only vault, so these tickets live here. Each ships in the established rhythm (small branch → web/CI
gate → merge `--no-ff` → push → record hash). Backend tickets present a short plan for approval before
editing (schema/API per CLAUDE.md §5/§15/§16).

Legend — **Effort:** S small · M medium · L larger. **Status:** `todo` / `in progress` / `done`.

---

## UX-05 — Warm cream/gold re-theme, both apps (feedback B) — **Effort S** — status: done
Founder chose **both apps** move off the current looks (web emerald/porcelain, web-ops dark green) to a
warm **cream ground + antique-gold accent** (light), with a dark mode. Presentational only.
**Acceptance:**
- Both `web/src/index.css` and `web-ops/src/index.css` carry the cream/gold token values (light default +
  `prefers-color-scheme` dark + `data-theme` override).
- Accent-on-fill contrast fixed via an `--on-accent` token (dark text on gold) so buttons/badges stay AA.
- No component logic change; web gate (oxlint/tsc/vitest/build) + guards green in both apps.
- Sweep: no leftover emerald/slate assumptions that break on the new palette.

## OC1 — Editable plans + “what's included” (feedback A) — **Effort S** — status: done
Plans today store only name + price, create/list only. Owner must be able to edit them and see what each
plan includes.
**Acceptance:**
- Migration: add `features jsonb` (list of strings) + `description text` to `billing_plans` (RLS n/a —
  global table); up/down verified.
- API: `PATCH /v1/billing/plans/{id}` (name/price/active/features/description) under the existing admin
  permission; validation + RFC7807 errors; contract tests (success, not-found, unauthorized).
- web-ops Financial: edit-plan form + a “what's included” features editor + display.
- Tests: service + API; no plaintext secrets.

## OC2 — Per-store spend-by-channel + ROI (feedback D) — **Effort M** — status: done
The `billing_charges` spine already stores per-store/per-month/per-type amount + cost. Add channel
granularity and a visible breakdown.
**Acceptance:**
- Extend `charge_type` (or add a `channel` column) to cover whatsapp / instagram / google_ads / seo /
  campaign / subscription / other; migration up/down verified; existing rows preserved.
- API: per-store spend rollup grouped by channel (spend, our cost, margin, and ROI vs attributed revenue
  where available), for a selectable month.
- web-ops: a spend-by-channel breakdown panel (grouped bars + totals) on the tenant view.
- Tests: rollup math (incl. zero/─ cases), isolation (per-org), API.

## OC3 — Plan-aware ticket priority + SLA (feedback C) — **Effort M** — status: done
Rank operator tickets by the tenant's plan tier + urgency, with SLA timers.
**Acceptance:**
- Admin ticket list carries the org's plan (join subscription→plan) and a derived tier.
- Sort by (tier desc, priority, severity, age); SLA target per tier; breach flag when past target.
- web-ops Queue: plan badge, SLA countdown, breach highlight, tier-aware sort.
- Tests: sort/priority ordering, SLA breach boundary, permission gating.

## OC4 — Tenant 360 performance profile (feedback E) — **Effort L** — status: done
Clicking a store opens a profile combining performance + spend (OC2) + plan (OC1) + tickets (OC3) +
existing insight reports.
**Acceptance:**
- API: per-tenant rollup (revenue trend vs prior, campaigns working/underperforming from the analytics
  engine), scoped to one org, admin-gated.
- web-ops: a `/stores/$orgId` profile page (performance strip, spend-by-channel, plan card, priority
  tickets, insight reports) — reuses OC1–OC3 pieces.
- Tests: rollup, isolation, API; no customer PII beyond what the operator plane already exposes.

---

## Tenant app polish (new — founder 2026-08-10)

- **TX1 Automations onboarding — worked examples + option guidance** (M) — **DONE** — the tenant Automations page
  (`web/src/components/WorkflowsSection.tsx`) is hard to operate. Add: a **library of ready-made example
  automations** (2 simple, 2 medium, 3–4 complex) the owner can read and "use as a template", and, for
  each **option** (trigger event, condition/CEL, the step types agent_task/human_task/wait/branch/emit/
  set/loop, and the server-locked guards) a plain-language **what it is / why you'd use it / how to fill
  it in** — like documented script arguments. Likely a declarative examples file in the jewelry pack +
  a help/reference panel + a "start from example" action. Presentational-ish; no new external actions.

## Forecast backlog (feedback F) — proposed, not yet scheduled

- **OC5 Churn-risk score + early alerts** (M) — composite health score; extends Customer Success at-risk.
- **OC6 Client-facing transparency report** (M) — the owner sees their own spend-by-channel + ROI (D on
  the tenant side) as a monthly statement.
- **OC7 Per-channel budgets & caps** (M) — monthly budget per channel/store; alert/pause at cap, wiring
  the existing `budget_exceeded` guard.
- **OC8 SLA-by-plan board** (S/M) — response targets per tier + a “about to breach” board (builds on OC3).
- **OC9 Operator alert feed** (M) — the operator's own notification bell (at-risk stores, SLA breaches,
  failed campaigns, stuck outbox) — mirrors the owner bell.
- **OC10 Cohort benchmarking** (M) — a store vs peer averages, turned into advice.
- **OC11 Per-tenant onboarding checklist** (S/M) — setup completion (WhatsApp, catalog, first campaign).
- **OC12 Invoices/statements from charges** (M) — monthly invoice per store from recorded charges.

(Also still parked from before: the multi-channel/advertising adapter track — email → Instagram → Google
Ads — which would feed OC2/OC4 with real per-channel spend.)

---

## Payments & receipts (new — founder 2026-08-10)

> "After I on-board the customer (after integrating the Razorpay API) I should be able to charge the
> customer and automatically the receipt is generated and sent to both WhatsApp and Email on file,
> similar to a Shopify charge receipt."

Here "the customer" = the **store owner** (GO's client/tenant). This is a multi-step feature, gated
behind real payment credentials (§10.4 — never charge without founder approval + live keys). Split:

- **PAY0 Email channel adapter (gated)** (M) — **DONE** (2448238→); **prerequisite**; the parked email track. httpx/SMTP behind
  a flag, off by default / simulated in tests, never sends without gate + approval. Needed for the receipt
  email and for OC2/OC4's per-channel data.
- **PAY1 Razorpay charge adapter (gated)** (M/L) — **DONE**; create/collect a charge against a tenant via Razorpay;
  **simulated until `razorpay_live_enabled` + real keys** (mirror the WABA/LLM gated-adapter pattern —
  httpx, off by default, mocked in tests, no real charge without founder approval). Records the charge in
  the billing model (OC1/OC2) + payment status; webhook verification for capture.
- **PAY2 Receipt generation** (M) — **DONE**; a Shopify-style receipt (HTML → PDF) from a charge: store + line
  items + amount + plan + date + tax fields; stored/retrievable; no secrets in the artifact.
- **PAY3 Receipt delivery to WhatsApp + Email** (M) — send the generated receipt to the tenant's phone
  (WABA send — already real-ready, MVP-076) **and** email (PAY0) "on file", via the approval/mediation
  boundary; idempotent (no double-send on retry); audited.

Dependencies: PAY0 (email) → PAY3; PAY1 (Razorpay) → PAY2 → PAY3. Onboarding must capture/verify the
tenant's phone + email + Razorpay customer/token first. Sequencing TBD with founder (likely after OC2–OC4,
and needs the Razorpay account + keys).
