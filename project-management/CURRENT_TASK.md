# Current Task

This file always describes exactly one active ticket. When a ticket completes, append its verified summary to
`IMPLEMENTATION_LOG.md` and mark this task as
`Completed — awaiting founder review`.

Do not replace this file with a new ticket until the founder explicitly
selects and approves the next ticket.

---

## Bulk import — **I1–I3 merged; I4 (MVP-077) done → TRACK COMPLETE** (2026-08-09) — I4 awaiting founder merge approval

**I4 photo extraction (MVP-077, gated-simulated):** `core/ingestion/extract_photo.py` — provider off →
deterministic placeholder row per image (`simulated_vision`, conf 0.5) so a photo batch flows through
review→load; provider on but vision unwired → fail-closed `provider_unavailable`. `POST /extract`
dispatches by `source_kind` (photo→vision, else CSV/XLSX). Rule Zero honoured (guard caught a "jewelry"
docstring ref, fixed). No migration/dep. **Verify:** ruff/mypy(150)/**guards 0**/**391 pytest** (2 new).
**The bulk-import track is COMPLETE** (I1 extract → I2 review → I3 load/revert → I4 photo). **Next:**
workflow engine (MVP-071–73) — last MVP gap → Sales dashboard → marketing-agent layer.

---

## Bulk import · I3 — **merged `79adc2f`, CI green** (2026-08-09)

**I3 load + 30-day revert (MVP-080):** `core/ingestion/load.py` — `load_batch` (confirmed rows →
`crud.create_item` [validate + identity dedup], stamped import_batch_id; DuplicateIdentity→skipped,
ValidationProblems→load_failed per-row; →loaded); `revert_batch` (≤30d: archive UNMUTATED items, list
edited-since as mutated_skipped; →reverted); `reap_old_batches` daily job. `POST /v1/imports/{id}/load
|revert`. **No migration** (import_batch_id existed). **Verify:** ruff/mypy(149)/guards/**399 pytest**
(3 new). **The CSV/XLSX bulk-import path (I1→I2→I3) is COMPLETE** — upload → review → load/revert.
**Next:** I4 (MVP-077 photo/vision extract, gated-simulated) → workflow engine → Sales → marketing.

---

## Bulk import · I2 — **merged `b4bc5c2`, CI green** (2026-08-09)

**I2 review queue (MVP-079):** `core/ingestion/review.py` — `validate` (advance extracted→validating→
review; flag missing_title [blocking] + duplicate_sku); confirm/edit→re-flag→confirm/reject a row;
bulk confirm-all (skips rejected+blocking); auto-approve gate (all ≥0.95 conf + no flags + ≥5% sample).
Endpoints `POST /validate`, `/rows/{seq}/confirm|reject`, `PATCH /rows/{seq}`, `/rows/confirm-all?auto=`
(catalog:write). No migration/dep. **Verify:** ruff/mypy(148)/guards/**396 pytest** (3 new); gitleaks.
**Next:** I3 (MVP-080 load + 30-day revert) → I4 (MVP-077 photo, gated). Then workflow engine → Sales →
marketing agent.

---

## Bulk import · I1 — **merged `ca763a0`, CI green** (2026-08-08)

**I1 CSV/XLSX extract + mapping (MVP-078):** `core/ingestion/extract_csv.py` — parse CSV (stdlib) +
XLSX (openpyxl) → `import_rows` (raw + normalized mapped to catalog fields; price ₹→minor; unmapped→
attributes; title-less flagged); **column-map remembered per source signature** (tenant_settings);
advance `extracting→extracted`/`failed`. `POST /v1/imports/{id}/extract` (catalog:write). Dep openpyxl
(MIT, approved). **Verify:** ruff/mypy(147)/guards/**393 pytest** (4 new: CSV map+flag; XLSX; saved-
mapping precedence; failure→failed); lock synced; gitleaks. No migration. **Build order:** I1 (078) →
**I2 (079 review queue)** → I3 (080 load+revert) → I4 (077 photo, gated). **Then:** workflow engine →
Sales dashboard → marketing-agent layer.

---

## Billing / P4.6 Financial — **COMPLETE: B1 `4e06f82` + B2 `01e2646` merged, CI green** (2026-08-08)

**B2 P4.6 Financial dashboard (web-ops):** `FinancialSection` at `/financial` — rollup cards (MRR /
service revenue / cost / **margin** / active clients from `platform_billing_rollup`) + plans manager
(list/create) + per-client billing (store picker → assign plan + record charge [amount + cost] + view
charges). Dashboard on `tenants:read`; writes gated `tenants:manage` (client + server). Cashflow/burn/
runway deferred (need expense/cash inputs). **Verify:** web-ops oxlint/tsc/**vitest 6**/build + guards +
gitleaks. **Completes P4.6 Financial** (billing B1 `4e06f82` + B2). **Sales dashboard = separate later
ticket** (GO-sales-pipeline model). **Next (founder sequence):** bulk import (MVP-077–080) / workflows
(MVP-071–73) / marketing-agent layer.

---

## Billing · B1 — **merged `4e06f82`, CI green** (2026-08-08)

**B1 billing model + operator CRUD:** migration 035 `billing_plans` (global tiers) + `billing_subscriptions`
(RLS, 1 active/client) + `billing_charges` (RLS: **amount + cost** → margin) + `platform_billing_rollup()`
**SECDEF** (MRR / this-month revenue/cost/margin / active clients). **Operator-only** `/v1/admin/billing/*`
(plans, per-client subscription + charges, rollup) — admin-plane + tenants:manage/read, scoped writes
audited w/ target_org, no tenant path, flag allowlist untouched. Model: managed-budget+margin, named
tiers (founder). **Verify:** ruff/mypy(146)/guards/**394 pytest** (5 new: margin=amount−cost + MRR via
rollup deltas; charges org-isolated; 403/401/404) + mig round-trip + lock intact + drill. **Next:** B2 =
P4.6 **Financial** dashboard in web-ops (from the rollup). **Sales** = separate later ticket
(GO-sales-pipeline model).

---

## Campaign SEND (top MVP gap) — **COMPLETE: C1 `291dd68` + C2 `1356b53` merged, CI green** (2026-08-08)

**C2 campaign compose/send UI (MVP-089):** frontend `CampaignsSection` at `/campaigns` (list + create
w/ approved-template picker + **send wizard**: audience preview → **type the count to confirm** [C5
typed-count gate; 409 shows the real number] → parks tier-3 approval → shows in Approvals queue) + a
tiny `GET /v1/campaigns/audience-preview`. Nav gated `campaigns:read` (staff excluded — pinned nav test
restructured). **Verify:** web oxlint/tsc/**vitest 50**/build; backend ruff/mypy(143)/guards + 12
campaign tests + scaffold import; gitleaks clean. **This completes campaign-send (C1 backend `291dd68`
+ C2 UI) — the top MVP gap is CLOSED.** **Next (founder priority):** per-client billing model (unblock
P4.6) → bulk import (MVP-077–080) / workflows (MVP-071–73) / marketing-agent layer.

---

## Campaign SEND · C1 — **merged `291dd68`, CI green** (2026-08-08)

**C1 campaign send execute path (MVP-075 / C5, full faithful spec):** migration 034 `campaign_sends`
(+RLS) + template/halt cols + widened status. `POST /v1/campaigns/{id}/send` **typed-count gate**
(409, no silent fix) → **tier-3 approval** → on approve (consumer) **staggered fan-out ≤500/hr** →
per-recipient gated `send()` template (consent/suppression re-check) → `campaign_sends` row → emit
`campaign.executed.v1` → executed. **Quality-halt** on opt-out spike / red Meta rating. `campaign_fanout`
hourly scheduler. Audience = consented + un-suppressed. Gated-simulated. **Verify:** ruff/mypy(143)/
guards/**407 pytest** (5 new incl. full fan-out 2/2 + typed-count 409 + tier-3 + reject + halt) + mig
round-trip + drill. **Next:** C2 web/ Campaigns wizard (audience preview → template → typed-count
review; the parked approval shows in the existing Approvals queue). Then per-client billing → bulk
import / workflows / marketing agent.

---

## Phase 4 · Operator/CEO console — **P4.1–P4.5 merged, CI green; P4.6 blocked on billing** (2026-08-08)

**P4.5 per-store drill-down (operator reads a store's agent reports):** migration 033 two **SECDEF**
fns `platform_store_reports(org)` + `platform_store_report(org, report)` (detail **scoped to org_id=
p_org**). `GET /v1/admin/tenants/{org}/reports(/{id})` gated on **`platform.insights:read`** +
admin-plane, **each read audited with target_org_id** (most sensitive P4 surface — actual insight
content). web-ops roster names link → `/stores/$orgId` `StoreReportsSection`. **Verify:** backend ruff/
mypy(141)/guards/**412 pytest** (6 new incl. cross-store id 404) + mig round-trip + lock intact; web-ops
lint/tsc/**vitest 6**/build. **The real-data Phase-4 dashboards (P4.1–P4.5) are COMPLETE.** **P4.6
(Financial+Sales) is BLOCKED** on a per-client billing model (revenue-vs-pass-through-cost Q open — see
VISION_INTAKE item 17 / [[go-revenue-model]]).

---

## Phase 4 · P4.4 — **merged `c1e202d`, CI green** (2026-08-08)

**P4.4 customer success (store health + at-risk):** migration 032 `platform_customer_health()`
**SECDEF** fn → one row per store (paused, ticket counts, days_since_activity, WoW revenue, computed
**`at_risk`** = paused OR urgent OR inactive>14d OR revenue halved) — **no PII**, flag allowlist
untouched. `GET /v1/admin/customer-health` (`platform.tenants:read` + admin-plane, audited); web-ops
`CustomerSuccessSection` at `/health` (at-risk-first + reason chips). **NPS/upsell deferred** (surveys/
billing — not faked). **Verify:** backend ruff/mypy(141)/guards/**406 pytest** (4 new: at_risk per
cause + healthy clear; 403/401/404) + mig round-trip + lock intact; web-ops lint/tsc/**vitest 6**/
build. **Next:** P4.5 per-store drill-down.

---

## Phase 4 · P4.3 — **merged `97663fc`, CI green** (2026-08-08)

**P4.3 executive + marketing (cross-store rollup):** migration 031 `platform_analytics_rollup(days)`
**SECDEF** fn → one row of platform-wide sums/counts over a window + prior window (WoW): revenue/
orders/leads/quotes (+prev), active_stores, campaigns_run, messages_sent, campaigns_analyzed,
attributed_revenue — **no PII**, flag allowlist untouched. `GET /v1/admin/analytics/rollup?days=`
(`platform.tenants:read` + admin-plane, audited); web-ops `AnalyticsSection` at `/analytics`
(Executive WoW cards + Marketing cards). **CAC/churn + impressions/CPL deferred** (need billing/ad data
— not faked; CAC/churn land with P4.6 billing). **Verify:** backend ruff/mypy(140)/guards/**402
pytest** (4 new: before/after deltas incl. prior-window; 403/401/404) + mig round-trip + lock intact;
web-ops lint/tsc/**vitest 6**/build. **Next:** P4.4 Customer-success, P4.5 per-store drill-down.

---

## Phase 4 · P4.2 — **merged `de937de`, CI green** (2026-08-08)

**P4.2 operational dashboard (what's breaking/delayed):** migration 030 `platform_operational_health()`
**SECDEF** fn → one row of curated COUNTS (outbox_pending/stuck, approvals_pending/overdue,
tickets_open/urgent, stores_paused), **no PII**, flag allowlist untouched. `GET /v1/admin/ops/health`
(`platform.tenants:read` + admin-plane, audited); web-ops `OperationalSection` at `/ops` (severity
cards; error detail deferred to GlitchTip). **Verify:** backend ruff/mypy(139)/guards/**398 pytest**
(4 new: before/after deltas + overdue≠pending; 403/401/404) + mig round-trip + lock intact; web-ops
lint/tsc/**vitest 6**/build. **Next:** P4.3 Executive+Marketing (cross-store analytics rollup).

---

## Phase 4 · P4.1 — **merged `98ecb05`, CI green** (2026-08-08)

**P4.1 cross-store roster (foundation):** migration 029 `platform_tenant_roster()` **SECURITY
DEFINER** fn → curated per-store rows (id/name/plan/status/created_at/paused/open_tickets/member_count),
**no customer PII**; reads RLS-protected settings/tickets/user_orgs via definer **without widening the
`app.platform_admin` flag** (least-privilege lock stays green). `GET /v1/admin/tenants`
(`platform.tenants:read` + admin-plane, **audited**); web-ops `StoresSection` roster table. **Verify:**
backend ruff/mypy(138)/guards/**394 pytest** (5 new: reflects state; curated-no-PII; 403/401/404) +
mig round-trip + lock intact; web-ops lint/tsc/**vitest 6**/build. **Founder-approved Phase-4 order:**
P4.1 foundation → P4.2 Operational → P4.3 Executive+Marketing → P4.4 Customer-success → P4.5 per-store
drill-down → P4.6 Financial+Sales (needs a per-client billing model; revenue-vs-passthrough Q open).
**Next:** P4.2 Operational dashboard.

---

## Security hardening (from audit #16) — **COMPLETE: S1 + S2 + S3 merged** (2026-08-08)

**S3 backup + tested restore (audit #16e) — the restore DRILL is the point:** `scripts/db_backup.sh`
/ `db_restore.sh` (guardrails: refuses `*prod*` + primary DB without `--force`) / `db_restore_drill.sh`
(dump→restore-into-scratch→verify table count + alembic head + org rows→drop→PASS/FAIL). **Drill runs
in CI** (`migrate` job) every push = continuous proof; `make backup-drill` locally (in-container).
`infra/db/BACKUP_RESTORE.md`; `/backups/` gitignored. **Verified pg16: 71 tables, head 9f9334d2999a,
orgs round-tripped → PASS.** No app code/migration/dependency. This **completes the security-hardening
initiative** (S1 secret scan + S2 error tracking + S3 backup/restore; audit #16 a/d/e closed, b/c/g
already strong, f N/A). **Next (per VISION_INTAKE sequence):** Phase 4 operator/CEO console.

**S2 error tracking (audit #16d) — MERGED `02038dd`, CI green — self-hosted GlitchTip (best UX + tight):** backend
`core/common/error_tracking.py` + `sentry-sdk` and frontend `@sentry/react` + `ErrorBoundary`, both
**off by default** (gated on a DSN) and **PII-scrubbing** (bodies/locals dropped; phone/OTP/email/
tokens masked, sensitive keys dropped, before send). Local `docker-compose.glitchtip.yml` + runbook +
`make glitchtip` so the dashboard UX is reachable; data never leaves our cloud. **Verify:** backend
ruff/mypy(137)/guards/`pytest unit+isolation` **389** (5 new); web oxlint/tsc/**vitest 50**(3 new)/
build; gitleaks clean. **Next:** S3 backup + tested restore (audit #16e).

**S1 secret scanning (audit #16a) — MERGED `bc6e4a6`, CI green:** recon verdict — **134 commits, zero
real secrets** (lone finding a false-positive param annotation). `.gitleaks.toml` + CI `secret-scan`
job (pinned gitleaks 8.30.1, full history, `--redact`) + `make secret-scan`. Scanner verified to still
catch a planted fake token. Sequence & rationale in DECISIONS 2026-08-08.

---

## Phase 3.5-eng · Analytics & Intelligence engine — **COMPLETE: A1–A4.6 done** (2026-08-08) — merged (A4.6 `9bbb031`)

**A4.6 owner Insights UI (frontend-only, engine's front door):** the store owner opens an insight and
drills through it as **four escalating questions by intensity** — *What happened?* (verdict) → *Why?*
(drivers) → *Show me the numbers* (full breakdown) → *Prove it* (evidence) — **all real stored data,
no AI, no wait**. A free-text **Ask Growth Operator** thread (human operator answers, no fabricated AI)
backs the rest. New `InsightsSection.tsx` + `api.ts` fetchers + `lib/insights` helpers/`QUESTION_LEVELS`
+ `/insights` route + nav gated `insights:read`. **Verify:** oxlint · tsc · **vitest 47** · build ·
guards 0 · backend regression `pytest tests/unit tests/isolation` **384**. **This completes the
analytics/intelligence engine (A1–A4.6).** **Next (per VISION_INTAKE sequence):** security-hardening
ticket → Phase 4 → marketing-agent framework layer.

**A4.5 owner⇄GO thread (the cross-tenant one):** migration 028 `insight_messages` (+RLS) with **split-RLS carrying a scoped operator INSERT** — owner posts own-org owner-messages; operator answers cross-tenant with `author_type='operator'` only (`resolve_report_org` SECDEF; owner GET/POST + operator `POST /v1/admin/insights/.../reply`, audited). **The least-privilege lock was updated to a tighter invariant** (flag on exactly `{support_tickets, insight_messages}`; INSERT-check only on insight_messages, scoped to operator) + teeth-tested (owner can't forge an operator message; cross-org read 404; non-operator 403). **Verify:** ruff · mypy core (**136**) · guards 0 · **388 tests** (unit + isolation + thread); migration 028 round-trip + RLS forced. **Next:** A4.6 (owner Insights UI). *(Also: a large founder braindump of post-MVP frameworks/tools/agents captured 2026-08-08 — see project-management/VISION_INTAKE.md.)*

**A4.2+A4.3+A4.4 (2026-08-08, one committed batch — the intelligence producers):** A4.2 `core/campaigns/producer` (deterministic — engine → stored `campaign_analysis` insight; `POST /v1/campaigns/{id}/report`); A4.3 migration 027 `tracked_competitors` + `core/competitors` CRUD (`campaigns:send` write / `insights:read` view); A4.4 `core/insights/agents` — **gated-simulated** competitor + marketing producers (off→deterministic `model=simulated`; on→fail-closed `provider_unavailable`), `POST /v1/insights/reports/generate`. **Verify:** ruff · mypy core (**135**) · guards 0 · **374 tests** (3 producer + 4 competitors + 4 agents + no-regression); migration 027 round-trip + RLS forced. **Next:** A4.5 (owner⇄GO cross-tenant thread).

**A2.2+A3.1+A3.2+A4.1 (2026-08-08, one committed batch):** the **campaign analytics engine** + the **insight-record framework**. Migration 025 `campaign_touches` → **exact deterministic first-touch attribution** + funnel + one-sample z-test ("real lift or noise") + drop-off diagnosis (`GET /v1/campaigns/{id}/analytics`); **unhackable ROI** (revenue only from immutable orders; cost = real sent × owner rate; org-isolated) + plain-language **drivers** (verdict→reasons, good/bad/neutral); migration 026 `agent_reports` → the layered insight record `verdict→drivers→full_breakdown→evidence` + read API. Multi-touch / configurable window / `campaign_metrics` rollup / confidence intervals **deferred to `PRODUCTION_DEPTH_BACKLOG.md`**. **Verify:** ruff · mypy core (**130**) · guards 0 · **369 tests** (12 analytics unit + 4 attribution + 6 reports + no-regression); migrations 025/026 round-trip + RLS forced. **Next:** A4.2 (campaign-analysis producer). *(A1+A2.1 previously merged `ab1aed0`.)*

Branch `feature/phase35-eng-analytics` (off main). *One engine, built once, scoped by plane (owner = distilled outcomes; operator/Phase 4 = full breakdown). LLM simulated. Migrations chain off 022, additive/flagged.* **A1 — event-facts + rollup foundation:** **migration 023 `business_metrics`** (org-scoped +RLS) + `core/insights/metrics.py` (compute_day/upsert_day/weekly_summary from the domain tables) + `core/insights/rollup.py` (scheduled `business_metrics_rollup`, daily 00:15, per-org, trailing 30 days, idempotent upsert; registered in scheduler) + `GET /v1/insights/summary` (WoW, `insights:read`) + Home **"This week"** outcome card (`lib/insights`). **A2.1 — campaigns model + persistence:** **migration 024 `campaigns`** (org-scoped +RLS) + `core/campaigns/` (service create/list/get + `record_execution`; `@consumer(campaign.executed.v1)` records send counts, wired in the worker; `POST /v1/campaigns` `campaigns:send`, `GET` `campaigns:read`). **Honest finding:** `campaign.*` events are defined but **not emitted by any flow yet** (no send-lifecycle) — the consumer is wired + ready; create makes a record now. **Verify:** ruff · mypy core (**127**) · **guards 0** · **full tests/unit 358** (+scheduler job-set, +campaigns import-clean) · A1 **6 pytest** + A2.1 **7 pytest**; migrations 023/024 up/down round-trip + RLS forced; `web` tsc·vitest **41** (+insights 4)·oxlint·build; real-HTTP smokes (summary WoW; campaign create→list→consumer→executed). **Next:** A2.2 (funnel + significance + drop-off — the "why").

## Phase 3 · Customer dashboard — **COMPLETE (3.1–3.6)**; 3.1–3.5 on main `9a6ebb5`, 3.6 pending merge (2026-08-07)

**3.6 Settings + autonomy volume-knob (Option A — wired live):** the owner's per-capability autonomy knob (Messaging/Pricing/Campaigns · Auto/Review) + a global **Pause** switch, **wired into the live approval gate** — `engine.evaluate_tool` gains a max-tier `_autonomy_floor` overlay (`auto` respects the pack tiers; else/paused forces approval). Since `max()` wins it can only *raise* a tier → the **`CORE_TIER4_ACTIONS` money floor is immovable at every knob position** (tested at auto + paused). Free-dial (`tighten_only=False`, retires the loosening block); added `autonomy.campaigns`+`autonomy.paused`; `GET /v1/settings/autonomy` (owner). `SettingsSection` = pause + Auto/Review per capability + locked floor panel + store profile + preferences (reply tone, quiet hours). The settings-change **audit already existed** (`write_setting` diff). Default `auto` (routine auto-sends; risky/tier-4 already park) → zero behaviour change until an owner tightens. **Verify:** ruff · mypy core (**121**) · **guards 0** · **full tests/unit 351** · autonomy-gate+settings+no-regression **30**; `web` tsc·vitest **37**·oxlint·build; real-HTTP smoke (free-dial write, pause+level persist, staff 403). **Deeper autonomy depth → `PRODUCTION_DEPTH_BACKLOG.md`.**

**3.4 Catalog & pricing (frontend-only — catalog backend already complete):** `CatalogSection` — searchable item grid (static **₹** base price vs computed **"Live rate"** badge, availability, generic pack attributes), **create / edit / archive** gated `catalog:write` (staff/viewer read-only), rupees↔minor conversion, surfaces the backend's attribute-validation 422s legibly; `lib/catalog`. No backend change (existing catalog endpoints/tests unchanged; a small `authed` error-detail improvement). **3.5 Customers / CRM:** new read-only module `core/customers/` — `GET /v1/customers` (list + lead/order counts), `GET /v1/customers/{id}` (profile + leads + conversations + **orders/purchase history**; 404 cross-org), RLS + explicit-org + `customers:read`; `CustomersSection` responsive master-detail (profile + consent + preferences + orders-with-total + pipeline + conversations). **Verify (3.4–3.5):** backend **+5 pytest** (customers list+counts / detail-history / cross-org-404 / 403) · ruff · mypy core (**121**); `web` tsc · **vitest 34** (+catalog 4, +customers 3) · oxlint (2 pre-existing) · build; real-HTTP smokes (catalog list/search shapes + 403; customers list/404/403). See below for the original 3.1–3.3 block.

## Phase 3 · Customer dashboard (3.1–3.3) — **Committed `8930be8`** (2026-08-07)

Branch `feature/phase3-dashboards` (off main). *The store-owner dashboard on real data — operational sections only; the CEO-grade analytics/math lives in the operator console (Phase 4), and the owner gets distilled outcomes + drill-down + an ask-GO thread once the analytics engine (Phase 3.5) lands (DECISIONS 2026-08-06).* **No migration, no dependency.** **3.1 Home + shell:** `core/insights/{service,api}.py` `GET /v1/dashboard/overview` — one RLS-scoped round-trip of four org-scoped counts (pending approvals / open conversations / active catalog items / open tickets), gated `insights:read`; `web/` expanded to the full role-gated **8-section** shell (Home · Approvals · Conversations · Catalog · Customers · Support · Team · Settings), permission-based nav mirroring `permissions.py`, a Home with KPI tiles (loading/empty/error) + tasteful placeholders for the not-yet-built sections (one-file swaps). **3.2 Approvals queue (HITL core):** `GET /v1/approvals` now returns `matched_rules` + a typed `ApprovalSummary`; `ApprovalsSection` renders each parked draft (friendly title, body, price if a quote, tier badge, the "why" chips, expiry) → **approve / reject(+reason) / edit-then-approve** (resolve unchanged — keeps the tier-raise guard), gated by `approvals:resolve` (viewer read-only). **3.3 Conversations & leads:** new read-only module `core/conversations/{service,api}.py` — `GET /v1/conversations` (inbox + contact + last-message preview + count), `GET /v1/conversations/{id}` (thread, messages ascending, 404 cross-org), `GET /v1/leads` (pipeline), all RLS + explicit-org-scoped, gated `conversations:read`; `ConversationsSection` = **Inbox** (master-detail list↔thread) + **Pipeline** (leads by stage). **Verify (each ticket):** backend **19 new pytest** (3.1: counts/isolation/empty/401/400/403; 3.2: list+matched_rules/scoped/approve/reject/410/404/403; 3.3: inbox+last-msg/scoped/thread-asc/404-cross-org/leads/403) · ruff · mypy core (**118**) · import-clean; `web` tsc · **vitest 27** (roles 14, home 2, approvals 6, leads 3, conversations 2) · oxlint (2 pre-existing HMR warnings) · build; real-HTTP uvicorn smokes (overview 401-gated; approvals list→approve→pending 0). **Deferred (by design):** the layered outcome cards + ROI + campaigns come with the analytics engine (Phase 3.5-eng); the Settings section is a placeholder (**3.6**, next). **Next:** Ticket 3.6 (Settings + autonomy volume-knob).

## Phase 2 · Two apps + logins (separate customer + operator front-ends) — **Completed — awaiting founder review** (2026-08-06)

Branch `feature/phase2-apps` (off main). *Split the single `web/` app into two independently-deployable apps sharing the backend but no front-end code — the operator app never ships to a store (realises the Phase-1 separate-deployment decision).* **Dependency: `vitest`** (dev-only, both apps; founder-approved). **2.1** `core/tenancy/platform_router.py` `GET /v1/admin/me` (operator identity: role + platform permissions) behind the admin-plane gate (404 off) + `require_platform()` (403/401); moved `require_admin_plane_enabled` to `platform_admin.py` (shared). **2.2 Customer app (`web/`):** react-router + role-aware shell (nav from `/v1/me`; Team only for owner/manager); auth context (localStorage token, `/v1/me` hydrate, sign-out); support screens moved in, operator queue removed; **Team** section = Phase-1 **invite-with-role** (picker offers only roles ≤ your own); `scripts/dev_make_owner.py` + `make make-owner`. **2.3 Operator app (`web-ops/`, new, port 5174, dark theme):** separate login/token → gated on `/v1/admin/me` (operator / 403-not-operator / 404-plane-off / unreachable); role-aware nav by returned permissions (dev→queue+stores+debug, admin/staff→queue+stores, analyst→stores); cross-tenant support **queue** (list + inline resolve, resolve gated on `platform.tickets:resolve`); Stores/Debug = Phase-4/dev placeholders. **Verify:** backend **680 pytest** (+7: `/admin/me` per-role + 403/401/404) · ruff · mypy core (113); `web` tsc/vitest(10)/build + `web-ops` tsc/vitest(6)/build all green (oxlint exit 0, 4 cosmetic HMR warnings); both dev servers serve 200; live smoke (customer login→owner shell→report; operator per-role queue access). **Deferred:** store-onboarding flow (`make make-owner` is the local stand-in); front-ends not in CI yet (ops follow-up); Stores/Debug placeholders (Phase 4). **Next:** Phase 3 (customer dashboards).

## Phase 1 · Two-plane RBAC (tenant owner/manager/staff/viewer + platform dev/admin/staff/analyst) — **Completed — awaiting founder review** (2026-08-06)

Branch `feature/phase1-rbac` (off main). *First slice of the multi-plane program (separate apps + full role matrix + ROI-now — founder-approved 2026-08-06); the identity foundation every dashboard gates on. Enterprise control-plane / data-plane split.* **No dependency.** **Ticket 1.1 (tenant):** retired the `founder` role + `platform:admin` permission (a tenant role holding a platform permission was a latent cross-tenant escalation — closed; zero `founder` memberships verified first); `core/tenancy/permissions.py` roles owner/manager/staff/viewer + full grid (+`conversations:*`/`customers:*`/`campaigns:read`/`insights:read`/`members:manage`/`billing:manage`, deny-by-default until Phase 3) + `ROLE_RANK`/`can_grant_role`; **migration 021** (`user_orgs`+`invites` CHECK widened → new roles, RBAC catalog reseeded, drift-tested); **invites carry a role** (can't grant above own rank); re-gated `api_keys`+`ops` (own-org ops mislabeled `platform:admin`) → `org:manage`. **Ticket 1.2 (platform):** `core/tenancy/platform_permissions.py` **separate** namespace (`platform.*`) roles dev/admin/staff/analyst; **migration 022** `platform_admins.role` (default admin, CHECK); `require_platform(perm)` (allowlist + expiry + role + permission + audited flag); support endpoints gate on `platform.tickets:read`/`:resolve`; `grant-admin --role`. **673 pytest** (+50): tenant matrix + grant-hierarchy + invite-with-role + CHECK-rejects-founder + drift; platform matrix + per-role 403 (analyst) + **plane-separation (namespaces provably disjoint)** + migrations; + live per-role smoke (dev/admin/staff see cross-tenant tickets, analyst 403, tenant-owner token 403). ruff · mypy core (112) · migrations 018→022 round-trip. Migrations 021/022 not in the vault (BLOCKERS #21). **Deferred:** a member role-**change** endpoint (invite-with-role covers new members; changing an existing member is the Phase-3 members UI); the new tenant perms are unused until their features ship. **Next:** Phase 2 (two apps + logins).

## Support-01 · Support tickets — owner-raises → operator queue → resolve — **Completed — awaiting founder review** (2026-08-05)

Branch `feature/support-tickets` (off main). *First slice of the **Growth Operator control plane** (a separate cross-tenant operator app, distinct from the store-owner console — founder-directed 2026-08-05).* A store owner reports an issue from their console; it lands in the founder's operator queue with **priority + severity**; the operator triages/resolves; the owner sees the resolution. **No new dependency.** **Migration `ae1b311f9373` (018):** `support_tickets` (org-scoped) with **split-by-command RLS** — `p_read`/`p_update` carry a **fail-closed platform-admin exception** (`org_id = app.org_id OR app.platform_admin='on'`), but `p_insert` is **org-only**, so the operator can read/resolve across tenants yet can **never write into a tenant** (the isolation test proved a single `FOR ALL` policy would have leaked its USING into INSERT's implicit WITH CHECK — caught + fixed); `platform_admins` allowlist. Not in the vault schema/order (flagged, DECISIONS). **`core/tenancy/platform_admin.py` (new):** `platform_admins` is the **sole authority** for cross-tenant access — deliberately **NOT** the org-scoped `founder` role (which would be an isolation escalation); `get_admin_db` sets the transaction-local `app.platform_admin='on'` GUC only after the allowlist check (401 no token / 403 non-admin). **`core/support/`:** `service.py` (owner `raise_ticket`/`list_own`/`get_own`; operator `list_all`/`get_admin`/`update_ticket` — stamps `resolved_at`/`by`, writes the change to the affected **tenant's audit chain**) + typed `schemas.py` (Literal-validated priority/severity/status/category; owner `TicketOut` **hides** cross-tenant fields, operator `AdminTicketOut` adds `org_id`/`org_name`/`raised_by`) + `api.py` (`POST/GET /v1/support/tickets`, `GET/PATCH /v1/admin/support/tickets` behind `get_admin_db`); registered in main.py. **Dev tooling:** `scripts/grant_platform_admin.py` + `make grant-admin EMAIL=…`. **Frontend (`web/`):** real Vite/React screens on the existing auth — owner **"Report an issue"** + "My tickets"; an **operator queue** (all stores, resolve inline) that self-reveals only when the `/admin` call returns 200; `api.ts` client + react-query; `tsc`/oxlint/`build` clean. **599 pytest** (+23: Literal + service pre-DB validation unit; owner raise/isolation, non-admin **403**, operator cross-tenant list + **resolve→owner-sees-resolved + audit row**, 422s integration; **isolation** — org-scoped + fail-closed + admin-flag reads-all + **admin-flag cannot INSERT** + owner can't file cross-org; contract — route map + owner-view field hiding). ruff · mypy core (112) · migration round-trip · frontend build. RLS fail-closed; operator path audited; no external side effect; billing/SSO deliberately excluded. **Deferred (disclosed):** `support.ticket.raised.v1` **outbox event** (would break the vault `topics.yaml` drift test — needs a vault addition first; the queue reads by poll, so not needed for the loop); operator notifications; tenant roster/health + the rest of the control plane (next slices); wiring the owner console's message-understanding to a real model (separate approved track).

**Security hardening (2026-08-06, founder "google/apple-level", knocked out as a TODO list):** (1) **least-privilege lock** — exhaustive guard test that `app.platform_admin` is referenced by exactly one table's RLS and never in an INSERT check (teeth-verified); (2) **immutable `platform_access_log`** (migration 019, append-only trigger) recording every cross-tenant **read** (queue views) + write, separate from tenant audit chains; (3) **allowlist governance** (migration 020 `expires_at` → expired = fail-closed; `revoke` script + `make revoke-admin`; grants/revocations logged); (4) **admin plane off by default** (`admin_plane_enabled`, default false → `/v1/admin/*` 404s before auth, hiding existence). Plus a gated **fixed dev OTP** (`otp_dev_fixed_code`, dev-only, fail-closed, never leaked). Deploy-time controls (MFA/step-up, separate deployment+network isolation, dual-control, anomaly alerts, PII minimization) recorded in DECISIONS. **623 pytest** (+24). ruff · mypy core (111) · migrations 018→020 round-trip.

## MVP-076 · Imports migration + batch API — **Completed — awaiting founder review** (2026-08-05)

Branch `feature/mvp-076-imports-batch-api` (off main). *"Onboarding uploads photos/CSVs and tracks a batch through the pipeline."* The #4 (imports) foundation — extraction/review/load are 077–080. **Dependency: `python-multipart`** (founder-approved; required for FastAPI file uploads). **Migration `3c7f4aa8f204` (017):** `import_batches` (+RLS: source_kind, state, filename, byte_size, image_count, row_count, storage_ref, stats, created_by→users) + `import_rows` (+RLS: batch_id→import_batches, seq, raw/normalized/confidence/flags, state, UNIQUE(batch_id,seq)); per the migration-order doc, not in the vault schema (flagged); round-tripped. `core/ingestion/state.py`: the batch **state machine** — `BatchState` + `advance` (legal-only), `is_terminal`; `failed` is **resumable**. `core/ingestion/storage.py`: in-process blob store (real object storage at go-live). `core/ingestion/service.py`: `create_batch` (caps ≤500MB/≤200 images/≤5k CSV rows → `CapExceeded`+chunking hint → 422; emits `import.batch_state`), `transition` (legal-only + emit), list/get/rows. `core/ingestion/api.py` (registered in main.py): `POST /v1/imports` multipart (`CATALOG_WRITE`) + list/get/rows (`CATALOG_READ`) + **`GET /{id}/stream`** SSE relay (`StreamingResponse`, `text/event-stream`, block ≤2s off the Redis event stream). **576 pytest** (+17: exhaustive **legal-only** state property, **failed-resumable**, **5k-row cap→422+chunking hint** unit+API, **SSE delivers<2s** filtered+terminal-closing, multipart create+list+404, legal/illegal transition emits, `import_batches`/`import_rows` **cross-tenant isolation**). ruff · mypy core (106)+migrations · round-trip. RLS fail-closed; caps server-authoritative; no external side effect. **Deferred (ticket split):** extraction 077 (photos) / 078 (CSV + **Excel** via openpyxl), review 079, load/revert 080; real object storage; multi-image blob manifest (077); xlsx row-cap at extraction.

## #2 · Close the send loop (approved/auto reply goes out) — **Completed — awaiting founder review** (2026-08-05)

Branch `feature/close-send-loop` (off main). *"The grounded reply is actually sent (simulated) and recorded, tier-gated."* **No migration, no dependency.** `core/mediation/tools.py`: `_messages_send` (was a stub raising `approval_required`) now runs the gated send path — resolves the conversation, mints the send authorization (audit capability + single-use execution token) **in the proxy's session**, calls `send()`, returns a structured result; a `SendRefused` (e.g. unledgered figure) → `{"sent": False, "refused": code}` (never raises/trips the breaker). `core/channels/whatsapp/send.py`: `send()` +optional `session` param (+ `_send_session` helper) — reuses the caller's transaction when passed (a 2nd `org_scoped_session` inside the proxy **deadlocks** on the per-org advisory xact lock — the root bug found + fixed); standalone callers keep the two-phase self-committing behaviour (normalizer + existing send tests unchanged). `core/runtime/executor.py`+`graph.py`: the executor routes the reply through `messages.send` at RESPOND (conversation-bound runs) — `deps.execute_tool("messages.send", {body, conversation_id, message_class:"transactional"})`; **pending** (tier≥2) → `_park_send` (checkpoint before respond → resume re-sends, approved) → sends on approve; tier1 auto-sends; reject sends only the safe close; `conversation_id` threaded through `_drive`/`start_run`/`resume_*`. **559 pytest** (+6: messages.send delivers+records, unledgered figure→structured refusal, executor routes reply, priced reply parks, **full real-proxy run auto-sends tier1**, **priced reply parks→sends on approve**). ruff · mypy core (102) · guards (runtime-not-tools clean — `core.channels` only in the tool layer). No real external send (`MetaClient` simulated); all five send gates enforced. **Completes the customer-inquiry chain (objective steps 5–9).** **Deferred:** real Meta (go-live); passed-session loses queued-row-durability (fine while simulated); model-composed quote **figure_refs** plumbing; archetype-derived `message_class` for marketing; instance-manifest signing at install (MVP-061 seam).

## Executor→composer wiring · Prompt activation pipeline — **Completed — awaiting founder review** (2026-08-05)

Branch `feature/executor-composer-wiring` (off main). *"A routed run composes a real grounded prompt (base+vertical+tenant), not the skeleton — the remaining half of grounded drafts."* **No migration, no dependency.** **Discovery (flagged):** the composer (MVP-059) existed but had **0 `prompt_bindings`** to render, base layers were **never seeded**, and the executor still used the MVP-055 skeleton — so this built the **full activation pipeline** (founder-approved "do the full pipeline"), not a thin wiring. `core/prompts/base_layers.py` (new): `ensure_base_layer` idempotently seeds the platform base layer from `prompts/base/<archetype>.md` (global; None when absent → skeleton fallback). `core/packs/installer.py`: new `_activate_prompts` step (after `bindings_instances`) — per concierge (instance, task): base + `generate_tenant_layer` (from settings) + the pack vertical layer (binding task `catalog_answer`→ vertical anchor `catalog` via `prompt_layer.ref`) → `pin_binding`; skips on missing base/vertical or `IncompatiblePin` (install never fails). `core/runtime/graph.py`+`executor.py`: `Deps.compose` + `_make_compose(org, instance, persona)` → resolve active binding → `composer.render`, **skeleton fallback** on no-binding/error (composition never blocks a run); `composed_prompt_hash` now grounded; wired in `start_run`/`resume_run`/`resume_after_approval`. `prompts/base/concierge.md` version 1.0→1.4 (matches the vertical's `>= 1.4`). **553 pytest** (+4: install pins all 4 concierge bindings w/ base+vertical+tenant; no-base archetype skipped; grounded non-skeleton compose w/ deterministic hash; missing-binding→skeleton) + live smoke (install→4 bindings + 1 base + 4 tenant layers; run composes `# base.concierge v1.4 … Identity & safety …`). ruff · mypy core (102) · guards (runtime-not-tools clean). **Completes grounded drafts** for the concierge (inbound→route→**grounded prompt**→catalog-grounded reply→tiered approval→audit). **Deferred:** base layers for nurture/campaigner/ops/support (concierge only; others skeleton); tenant-layer re-generation on settings change; the `_satisfies` `">= "` space-parse quirk (compat lenient; latent).

## #20 · Tool→action bridge (pack tiers fire) — **Completed — awaiting founder review** (2026-08-05)

Branch `feature/tool-action-bridge` (off main). *"Make the MVP-044-seeded pack tier rules actually take effect."* **No migration, no dependency.** The pack rules key on abstract actions (`action.quote.send`) but the proxy asked the engine by tool name (`messages.send`) → nothing matched → everything over-approved at fail-safe tier-2. `core/approvals/engine.py`: `TOOL_ACTIONS` + `resolve_actions(tool, params)` (tool → abstract-action family; `messages.send` adds `action.quote.send` when it carries a price — structured `amount_minor` or the largest figure parsed from the body via MVP-054 `extract_amounts` — "a message with a price is a quote") + `evaluate_tool(...)`; refactored the matcher into `_contributors(...)` so single-action `evaluate` and the family `evaluate_tool` share it and the "no rule → tier-2" fallback applies **once** (a small no-discount quote falls back to the message tier). `core/mediation/proxy.py`: `_engine_tier`→`evaluate_tool`. `verticals/jewelry/agents/bindings.yaml`: `has()`-guarded optional-attribute conditions (`discount_any`, `escalation_triggers`) so an absent field is "not met" not fail-safe-match. **549 pytest** (+11: mapping+quote detection unit; plain-reply→tier1 / high-value-quote→tier2 / small-quote→tier1 / discount→tier2 / broadcast→tier3 integration) + a **real-proxy smoke** (plain reply sends, ₹1.5L quote parks `ApprovalPending(tier=2)`). ruff · mypy core (101). Engine fail-safe semantics unchanged; platform tier-4 still always tier-4. **Resolves BLOCKERS #20.** **Deferred:** free-text-only discount detection (agent should pass structured `discount_minor`). This completes the **tiering** half of grounded drafts; the remaining half is the executor→composer wiring.

## MVP-044 · Pack seeding: policies + prompt layers — **Completed — awaiting founder review** (2026-08-05)

Branch `feature/mvp-044-pack-seeding` (off main). *"An installed pack's rules + prompt layers land in their registries."* **Scope (founder-approved):** prompt-layers + approval-policies; `workflow_definitions` deferred to MVP-072. **Correction discovered:** prompt-layer seeding (`_seed_prompt_layers`) was **already implemented** — 9 candidate layers land on install (the "0 rows" was just no persisted install); the real work was `_seed_policies` (a deferred stub). **`core/packs/installer.py`:** `_seed_policies` inserts `approval_policies` (scope='pack') from each binding's `tier_defaults` — `action_type`=`applies_to` **verbatim** (AC = fidelity to the pack), `cel_expr`=condition, `timeout_s`=parse(`30m`→1800), `on_timeout`=map(`hold_and_remind`→`hold`), `approver_chain`=[approver], `confirm_kind`=confirm; idempotent per (pack, action, description); removed `policies` from `DEFERRED_STEPS`. **Migration `b6456b200baa`:** `p_pack_ins` INSERT RLS — app_rw (installer, in the tenant txn) may seed a global `scope='pack'` row but **not** `scope='core'` (platform tier-4 stays owner-only); mirrors `prompt_layers` but tighter; round-tripped. **`verticals/{jewelry,kirana}/install.yaml`** `deferred_steps`→`[workflows]`; existing installer/e2e/index/settings fixtures now delete `approval_policies` on teardown. **538 pytest** (+4: **seed-vs-pack diff = ∅**, re-seed idempotent, domain field mapping, **RLS-tightness** app_rw can't forge a core rule; + install seeds 8 policies/9 layers) + live smoke (4 concierge layers w/ real content + 8 tier rules incl. tier-2 quote / tier-3 broadcast). ruff · mypy core (101)+migrations · round-trip. **Deferred (disclosed):** **tool→action bridge** (BLOCKERS #20 — seeded pack tiers key on `action.quote.send` etc. but the proxy queries by tool name, so they don't fire yet; drafts fail-safe tier-2); `workflow_definitions` seeding (MVP-072); wiring the real **composer (MVP-059) into the executor** so runs use the seeded layers vs the skeleton prompt (separate follow-up).

## MVP-056 · Planner routing — **Completed — awaiting founder review** (2026-08-04)

Branch `feature/mvp-056-planner-routing` (off main). *"Inbound traffic gets classified and routed to the right archetype+task under global guards."* **No migration, no dependency** (reads the pack + bindings). Connects the built inbound channel (`msg.received`) to the executor/proxy/approvals spine. `verticals/jewelry/agents/bindings.yaml`: added `planner.intent_keywords` (16 intents — declarative pack authoring). `core/packs/taxonomy.py` (new): `load_taxonomy(slug)` builds `intent→(archetype,task)` + keywords + `frequency_cap` + concierge fallback from the pack's `bindings.yaml` — **loaded through the pack layer** (the DB `agent_bindings` never persists `intents`; ticket said "from agent_bindings" — flagged, DECISIONS; Rule Zero clean, `core/` reads pack config by path, never imports `verticals/`). `core/runtime/planner.py` (new): `@consumer` on `msg.received.v1` → **classify** (`classify` — longest pack-keyword match, gated-simulated; real small-model at go-live) → **route** (`route_message`; unknown→concierge+clarify) → **3 guards** (`is_tenant_paused`=org.status≠active; `suppression_blocks` mirrors the send-path rule; `frequency_cap_blocks` daily per-contact, transactional/active-conversation exempt) → **`start_run`** against the org's active instance for the routed archetype (`trigger=msg.received`, `input={body,intent,task,clarify}`). `core/worker.py` registers the consumer. **534 pytest** (+16: **routing_golden 20/20**, fallback+clarify, classifier longest-match; guard matrix incl. **cap blocks 2nd marketing touch same day** + suppression all-vs-marketing + paused; consumer path enqueues to active concierge + drops on paused/suppressed/no-instance) + live smoke (worker registers planner on `msg.received.v1`). ruff · mypy core (101) · guards (runtime-not-tools clean). **Deferred (disclosed):** real classifier model (go-live seam); **`support` archetype not seeded** in `agent_archetypes` (pack references it — routes resolve but find no instance; pre-existing gap); send-path call to `record_marketing_touch` (sender is a later ticket — cap+guard wired/tested); multi-pack arbitration (out of scope).

## MVP-064 · Model routes + failover — **Completed — awaiting founder review** (2026-08-04)

Branch `feature/mvp-064-model-routes-failover` (off main). *"Each task class uses the right model with a resilient failover chain — primary → secondary → holding template — with per-route/run cost logging."* **Posture: Option A (gated-simulated, founder-approved)** — built over the deterministic simulated provider; real vendors drop in at go-live with no change to `routing.py`. **No dependency.** **Migration `3680972ace7a`** — `costs_lite` (org-scoped, **+RLS**: `run_id`→agent_runs, `node_key`, `provider`, `model`, `outcome`, `tokens_in/out`, `cost_usd`) + idempotent **seed** of `model_routes` (`default`/`classify`/`converse`/`campaign`, each `anthropic` primary + `openai` fallback); lands ahead of the migration-order doc (additive, flagged — DECISIONS 2026-08-04); round-tripped, roles re-applied. `core/runtime/model.py`: `Provider` protocol + `SimulatedProvider(name)` + **`get_provider(name)`** — the gated seam (every provider name → simulated client until `llm_provider_enabled`; real clients register in `_REAL_PROVIDERS` at go-live, fail closed until wired). `core/runtime/routing.py` (new): **`RoutingModel`** (a `Model`) — per turn loads the route for the `node_key` (→ seeded `default` → hard-coded fail-safe), walks **primary → fallbacks**, returns the first success, logs each attempt to `costs_lite` (route+run attribution); **all providers down → holding template** (static no-tool reply, zero successful LLM output) + `alert.ops`. **Executor:** `start_run`/`resume_run`/`resume_after_approval` now build `RoutingModel(org_id, run_id, redis)` where they used `default_model()` — injected `model=`/`deps=` still override, so existing runtime suites are untouched. **518 pytest** (+7: failover-to-secondary, all-down→holding+alert+zero-success, cost attributed to route+run, default-chain fallback, per-provider cost estimate, `costs_lite` cross-tenant isolation) + a **live smoke** (`start_run` with no model → executor built RoutingModel → routed via default → 2 `costs_lite` rows, run succeeded). ruff · mypy core (99) + migrations. **Deferred (disclosed):** real vendor clients + real per-token pricing (go-live); **dynamic routing** (out of scope — static routes); per-**task-class** node_key wiring into `model_turn` (graph passes the constant `priya.reason` → resolves via `default`); a costs dashboard/rollup (rows written; digest surface later).

## #16 · Worker + scheduler process entrypoints — **Completed — awaiting founder review** (2026-08-04)

Branch `feature/mvp-028-scheduler-worker-entrypoints` (off main). *"Actually run the registered consumers, jobs, and outbox relay — the frameworks (MVP-026/028) shipped tested, but the two process entrypoints were still `sleep(3600)` placeholders (BLOCKER #16)."* **No migration, no dependency.** `core/worker.py` — `_install_consumers()` imports the three `@consumer` modules (msg.received **logger**, **approval.requested→notify owner**, **approval.resolved→resume parked run**), then `run_worker(stop)` runs the **outbox publisher** (outbox→Redis-streams relay) + one **`run_consumer`** per handler, with graceful SIGTERM/SIGINT shutdown (in-flight batch acked → no loss). `core/scheduler.py` — `_install_jobs()` registers the canonical set (**approval_ladder** every min, **trust_ledger_settle** hourly, **embeddings_batch** every 5 min via `SimulatedEmbedder`, **dedupe_prune** daily 03:30) then `run_scheduler_process(stop)` ticks under the per-(job, minute) Redis lock. Added a one-line public `scheduler.clear()` so the entrypoint installs its job set authoritatively/idempotently. **511 pytest** (+5: consumer/job registration completeness, **lock-proof** — a 2nd scheduler that minute is a no-op, cron routing at 00:00, graceful stop for both) + a **live-broker boot smoke** (worker booted 3 consumers + publisher; scheduler installed 4 jobs and fired `approval_ladder`; both shut down cleanly). ruff · mypy core (98) · scaffold import guard. Everything downstream stays **gated-simulated** — no real external action. **Deferred (disclosed):** the real **embedding provider** (BLOCKER #16 remains — founder decision; the batch is wired, only the simulated→real swap is left); the `jobs_runs` observability table (MVP-028 already deferred it — structured logs cover the acceptance); the **flags fast-path subscriber** loop (not built — the executor still loads flags per-run); the compose **env-var mismatch** (BLOCKER #1) a real container boot needs.

## MVP-063 · Failure contract + circuit breaker — **Completed — awaiting founder review** (2026-08-04)

Branch `feature/mvp-063-failure-circuit` (off main). *"A failing agent pauses itself loudly instead of flailing at customers."* **Migration `da3474bd3cdb`** — the org-scoped `incidents` table (+RLS, forced): `org_id`, `run_id`→`agent_runs`, `instance_id`→`agent_instances`, `kind`, `severity`, `title`, `action_type`, `detail`, `status`(open/resolved), `opened_at`/`closed_at`; lands **ahead of its scheduled 018/MVP-074 slot** (additive, no FK conflict — flagged, DECISIONS 2026-08-04); `circuit_open` was **already** an allowed `agent_instances.status` value (no status migration); upgrade+downgrade round-tripped, roles re-applied. `core/runtime/failure.py`: the breaker state machine — consecutive-failure count in Redis (per instance, 1h TTL), incidents+status in Postgres (RLS-scoped). `note_failure` — a **tier-2+** failure auto-opens a `tier2_failure` incident **with the run link** + `trust.record_incident` (reset + 14d tighten, MVP-070); increments the streak; the **2nd consecutive** failure opens the circuit (`_open_circuit`: instance→`circuit_open`, `circuit_open` incident, `alert.ops.v1`). `note_success` resets; `is_circuit_open`; `close_circuit` (manual resume) clears the counter, reactivates the instance, resolves the incident so held work drains. **Executor:** `start_run` **holds** (records an interrupted `circuit_open` run) when the instance's circuit is open; `_drive` retries a hard `provider_unavailable` step **once in place** (re-issues the same tool, no model re-consult) then trips + interrupts on the 2nd failure; a clean tool resets the streak; `_make_proxy_tool` tags the tool's consequence tier onto an error. **Proxy:** a raising tool impl → structured recoverable `provider_unavailable` (the failure contract), not a crashed run. **506 pytest** (+6: 2nd-consecutive→circuit+alert, tier-2→incident-with-run-link+tighten, clean-resets-streak, `close_circuit` recovery, **end-to-end** persistent-failure→trip→interrupt→held→drives-again; `incidents` cross-tenant isolation as `app_rw`). ruff · mypy core (98) + migrations · guards 17 (runtime-not-tools clean). **Deferred (disclosed):** the **<30s alert-delivery** surface (alert emitted immediately; the ops consumer that reaches the owner is #16 worker/scheduler wiring); the **incident-detector** for non-runtime incidents (an input); pack-configurable **retry/threshold** (constants 1 / 2); migration **018 (MVP-074) must skip** re-creating `incidents`.

## MVP-062 · Budgets, rate windows, untrusted narrowing — **Completed — awaiting founder review** (2026-08-03)

Branch `feature/mvp-062-limits` (off main). *"Blast-radius controls: cap spend/sends and shrink the tool surface after untrusted content."* **No Postgres migration** (Redis-backed with daily-key TTLs). `core/mediation/limits.py`: **`check_rate`** — a true 60s **sliding window** per (instance, tool) via a Redis sorted set (a burst can't slip a fixed-minute boundary; a denied call consumes no slot); **`check_budget`/`record_budget`** — per-(instance, kind) daily counters (the proxy checks the cap, the side-effect boundary records usage); **narrowing lifecycle** — `mark_untrusted`/`is_untrusted`/`clear_untrusted` + `result_is_untrusted` (a tool in `{web_fetch, file_ingest, forwarded_content}` or a result flagged `content_class=external_untrusted`). **Proxy wired:** step 5 rate → sliding window; step 6 budget → daily send cap (+ a breach logged for telemetry); step 3 narrowing now reads the run's untrusted flag (not just the static ctx), and after a tool returns external content the run is marked untrusted; the executor's `resume_after_approval` **clears** the flag (approval = human boundary; a new customer turn is a fresh run anyway). **500 pytest** (+7: sliding-window accuracy/burst-then-slide, budget check/record/exhaustion, narrowing mark/clear lifecycle, and the AC — **web_fetch → messages.send denied, catalog.search still allowed**). The MVP-060/061/069 proxy+executor tests stay green (their `FakeRedis` gained the sorted-set ops). **Deferred (disclosed):** the budget **record** at the real send boundary (belongs in the `messages.send` tool impl when it's wired to the MVP-054 send path — same seam as MVP-069's send-on-approve); the `telemetry_events` **dashboard table** (breach is a structured log for now); **tokens/spend** daily budgets (sends is the hard external cap wired; tokens/spend are run-level, on `agent_runs`).

## MVP-061 · Manifest compiler + signing — **Completed — awaiting founder review** (2026-08-03)

Branch `feature/mvp-061-manifest-compiler` (off main). *"An instance's allowed tool surface is compiled, signed, and pinned to every run."* **No new migration** (`agent_instances.permission_manifest` exists since 008 — recompile updates its value). `core/mediation/manifest.py`: `compile_manifest` intersects **level-1 archetype `capability_allowlist` ∩ level-2 pack `tool_grants` ∩ level-3 tenant grants** (optional narrowing) — a read-only tool (`.read`/`.search`) skips the tier gate, every other granted tool `requires_tier_eval`; `sign`/`verify` add + check an **ed25519** `hash` (sha256 of the canonical body) + `signature` (over the body); `recompile_instance` re-compiles from current grants, re-signs, and re-pins the instance. Config adds `manifest_signing_seed` (dev default + SOPS). **The mediation proxy now verifies the full chain per call** (MVP-060 checked only the hash): the run's pinned hash matches, the manifest's hash matches its body, the **signature is valid**, and the pin is **fresh** (equals the instance's current compiled manifest) — a forged/stale/tampered manifest denies every call and ≥3 abort the run. The executor pins the **body** hash into `agent_runs.permission_manifest_hash` (so it matches what the proxy computes). **493 pytest** (+9: intersection ∩ narrowing, read-only vs tier-eval, sign/verify roundtrip, tamper fails verify; recompile pins a signed intersection, **stale manifest denied until recompile**, **tamper → sig fail → run aborts after 3**). MVP-060/069 tests updated to sign their manifests. **Deferred (disclosed):** the **level-3 tenant-grants source** (the compiler accepts `tenant_allow`, but no tenant-grants table/UI exists yet — narrowing is a no-op by default); the **automatic recompile-on-grant-change trigger** (`recompile_instance` is the function; wiring it to fire when grants change is the seam — AC2 partial); budgets are taken from `budget_caps` (may lack the tokens/spend/sends-day keys); `requires_tier_eval`/`read_only` derived by a name heuristic (no explicit grant flag).

## MVP-070 · Trust ledger job — **Completed — awaiting founder review** (2026-08-03)

Branch `feature/mvp-070-trust-ledger` (off main). *"Clean approvals accumulate; incidents reset and tighten."* **Migration `30b7edf76a9d`** — adds `approvals.trust_settled` (a per-approval settled marker, so the settle increment is idempotent) + a partial index on the unsettled tier-2 set; **flagged deviation** (the ticket said DB = "trust_ledger rows"; the marker is required for idempotency — DECISIONS 2026-08-03, founder pre-approved); additive, round-tripped, roles re-applied. `core/approvals/trust.py`: `settle` (hourly per-org) adds **+1 `clean_approvals` per tier-2 approval whose 72h window passed with no incident in it**, marking each `trust_settled` (counted once); `record_incident` **resets** the counter + stamps `last_incident_at` + writes a self-expiring **14-day tier-2 `incident_tightening`** row (which the policy engine already honours); `demotion_offers` surfaces a loosen-one-tier **offer for the digest only — never auto-applied** (IDL-007). `run_trust_settle` registered hourly. **484 pytest** (+6: settle increments a 72h-clean approval + idempotent + skips inside the window; incident resets + 14d tightening self-expiry; **72h boundary — an incident at 71h59m blocks the increment**; demotion offer is digest-only, writes no policy). **Deferred (disclosed):** the **incident detector** that calls `record_incident` (out of scope — an input); scheduler **firing** of the hourly job (#16); the pack-configurable demotion **threshold** (constant 20 for now); the digest surface that renders the offers (insights, later); the demotion-**apply** meta-approval UI (out of scope per ticket).

## MVP-068 · WhatsApp interactive approvals + ladder — **Completed — awaiting founder review** (2026-08-03)

Branch `feature/mvp-068-whatsapp-approvals` (off main). *"An owner approves from a WhatsApp tap in seconds."* **Migration `bb65660f0771`** — adds notification-state columns (`notified_at`/`reminded_at`/`escalated_at`/`notify_ref`/`notify_channel`) to `approvals`; **the ticket said "Database changes: None" but the columns didn't exist** (neither in the split 014 nor schema.sql) — flagged deviation, founder pre-approved (DECISIONS 2026-08-03); additive, round-tripped, roles re-applied. `core/approvals/notify.py`: `render_card` (text form of the pack commitment card) + `compose_interactive` (✅ Approve / ❌ Reject buttons carrying the approval id); `notify_approval` sends (gated-simulated `SimulatedNotifier`) + stamps `notified_at`; a consumer on **`approval.requested.v1`** notifies the owner. **Reply routing:** `parse_button` (`approve:<id>`/`reject:<id>`) + `parse_text_decision` (✅/❌/approve/reject/yes/no/haan/nahi — the Meta-template hedge) → `handle_button_reply`/`handle_text_reply` → `service.resolve` (text resolves the latest pending). **Ladder** (`run_approval_ladder`, registered every minute, per-org fan-out): **remind** at 50% of the window, **escalate** at 75%, **expire** (safe-hold) at the deadline — tracked by the new columns. **478 pytest** (+24: button routing, text fallback incl. ambiguous, card render/compose, notify+consumer, button/text resolve, ladder remind/escalate/expire timing). **Deferred (disclosed):** real WhatsApp **interactive send** (Meta gated, not live) + the `tap→sent <10s` staging measurement; **scheduler firing** of the ladder (entrypoint still the MVP-028 placeholder, #16); wiring the webhook **normalizer** inbound button/text → `handle_*_reply`; the full pack **`commitment_card`** layout render (text summary for now); the **backup-approver** identity on escalate (sends a reminder to the same channel — the backup-routing chain is a follow-up).

## MVP-069 · Approval-parked run resume — **Completed — awaiting founder review** (2026-08-03)

Branch `feature/mvp-069-parked-resume` (off main). *"A parked run resumes exactly where it stopped with exactly one side effect."* **No migration.** The executor's `tool_call` node now flows **through `proxy.call`** (the simulated tool is gone; `start_run`/`resume_run` build a proxy-backed `execute_tool` from the instance's permission manifest — `deps` still fully overrides for hermetic tests, `model`/`respond` override just those). On a tier-2 `ApprovalPending` the run **parks**: `_park` creates the approval (MVP-067) linked to `run_id`, checkpoints with the cursor **left before `tool_call`** (so a resume re-issues the same call) and restores `pending_tool`, then interrupts. `resume_after_approval(run_id, org, decision)` is **idempotent** (a run no longer `interrupted` is a no-op → a double-resolve resumes once): **approve** re-runs the parked tool with `RunContext.approved={tool}` so the proxy skips the tier gate and executes it; **reject** routes straight to a **customer-safe close** (`SAFE_CLOSE_TEXT`) and the original action never runs. `core/runtime/resume.py` registers a consumer on `approval.resolved.v1` (org from the CloudEvents `subject`) that looks up the parked `run_id` and drives the resume — deduped per event id + the run-status guard = **exactly once**. **454 pytest** (+5: tier-2 parks + approval created, approve→execute exactly once, reject→safe close no execution, double-resolve→single resume, consumer wiring). MVP-055 chaos/isolation tests stay green (they inject full deps). **Deferred (disclosed):** wiring the resume **consumer into the worker** process (registration is by import; the worker/scheduler entrypoint is still the MVP-028 placeholder, cf. #16); the parked-tool→**real send** path (the `messages.send` registry impl still routes to the approval flow — executing an approved `messages.send` against MVP-054+066 is the remaining integration; the exactly-once semantics are proven with a benign tool).

## MVP-067 · Approval service + resolve API — **Completed — awaiting founder review** (2026-08-03)

Branch `feature/mvp-067-approval-service` (off main). *"An owner approves/rejects/edits pending actions; edits are re-evaluated."* **Migration** `9f90c8831001` (the `approvals` object table +RLS — logically part of the 014 group, landed now as the next revision per the founder-approved ordering; round-tripped, roles re-applied): `org_id, run_id (→agent_runs, the parked run for MVP-069), requested_by (→agent_instances), action_type, tier, payload, edited_payload, matched_rules, approver_user_id (→users), status (pending|approved|rejected|expired), decision_note, reason_code, audit_id, expires_at, decided_at`. `core/approvals/service.py`: `create_approval` (emits **`approval.requested.v1`**), `list_approvals`, `resolve` with **`SELECT … FOR UPDATE`** idempotency (a double-tap returns the first outcome, one resolved event), **edit re-evaluation** (edited payload re-run through `engine.evaluate`; an edit that **raises the tier is rejected with an explanation** — an owner can't rubber-stamp an escalated action at existing authority), **410 on expired**; emits **`approval.resolved.v1`** (the MVP-069 resume trigger). `core/approvals/api.py`: `GET /v1/approvals` (`APPROVALS_READ`), `POST /v1/approvals/{id}/resolve` (`APPROVALS_RESOLVE`, 404/410 mapped). **449 pytest** (+7: create+announce, approve/reject+resolved event, double-resolve idempotent, edit-raises-tier→reject, edit-same-tier→approve, expired→410). Approval events were already registered — no vault change. **Out of scope (per ticket):** owner **notification** (WhatsApp interactive) → MVP-068; the **parked-run resume** → MVP-069 (next).

## MVP-066 · Execution tokens — **Completed — awaiting founder review** (2026-08-03)

Branch `feature/mvp-066-execution-tokens` (off main). *"Side-effect services execute only decisions the engine actually made."* **No new migration** (`execution_token_jti` exists from migration 014); **no new dependency** (ed25519 via `cryptography`, already used for pack signing). `core/approvals/tokens.py`: `mint(session, org_id, ctx_hash, tier, ttl=10min)` issues a compact **ed25519-signed** token `{jti, ctx_hash, tier, exp}` and persists the (unused) jti; `verify` re-checks the **signature**, the **ctx hash** (a token for one action can't authorize another — a swapped payload also breaks the signature), the **10-minute expiry**, and **claims the jti single-use atomically** (a replay is rejected). `action_hash(org, action, resource)` binds a token to org+action+resource. Config adds `execution_token_signing_seed` (stable dev default, prod via SOPS). **Stub removal (the flag day):** `send.py`'s `_verify_execution_token` stub → real `tokens.verify` at Gate 2 (refuses `approval_required`); the normalizer STOP-confirm now mints a **real** token (dropped `_AUTO_CONFIRM_TOKEN`); every send test mints real per-send tokens. `grep` confirms **zero execution-token stub references** remain in `core/`. **442 pytest** (+6: valid-once, replay rejected, ctx mismatch, swapped-payload → bad signature, expiry, missing/malformed; all send/template/figure tests updated). **Deferred (disclosed):** the **campaign executor** verification (no campaign executor exists yet — latent); the **proxy token-attach** happens at the send caller (normalizer / the future approval-execution flow), since no side-effecting tool executes through the proxy at tier<2 yet; the **daily jti prune** job (scheduler entrypoint still the MVP-028 placeholder, cf. #16).

## MVP-065 · Policy engine — **Completed — awaiting founder review** (2026-08-02)

Commit `528bcbe` on `main`. **⚠️ Process deviation (disclosed):** this ticket was committed **directly to `main`** — the `feature/mvp-065-policy-engine` branch was never created (a §7.1 slip; the code is complete + green, but branch discipline was broken). Not rewritten (pushed history; force-push forbidden). Branch discipline resumed. *"Every side effect gets a deterministic tier from declarative rules."* **Migration 014** (`1993ba538f4f`, chains off the runtime migration per the approved ordering): `approval_policies` (core/pack **global** rows + **tenant** rows, custom RLS — read globals + own, write own), `trust_ledger`/`incident_tightening`/`execution_token_jti` (+RLS); round-tripped, roles re-applied. `core/approvals/engine.py`: `evaluate(session, ctx) -> Decision` loads rules for an `action_type` — **core tier-4 minimums (code) → pack defaults → tenant rows → active incident-tightening** — evaluates each rule's **CEL** against the `ActionContext`, and takes the **max tier** (matched rule ids recorded). Deterministic (`select_decision` is a pure, order-independent max — proven over **10k shuffles**); **CEL compile cache** keeps evaluation at **p95 0.878 ms** (budget 5 ms). `validate_tenant_rule` enforces **tighten-only** (a tenant rule may not lower a tier below the core/pack baseline). **The mediation tier check is now LIVE** — the proxy's tier step calls the engine by default (injectable for hermetic tests). **433 pytest** (+14: ap-01..05/13..15 semantics, determinism 10k, tighten-only, incident expiry, unknown fail-safe, compile-cache, p95 benchmark). **Out of scope (per ticket):** token minting + approval-object lifecycle → **MVP-066**. **Deferred (disclosed):** the DB rules-**loading** version cache (the CEL **compile** cache is in; DB load isn't the 5 ms target); the exact ap-* fixture suite lives in the vault (illustrative — tested the documented semantics, per §4); ed25519 token signing (066); 48h staging shadow-compare (rollout note).

## MVP-060 · Mediation proxy — **Completed — awaiting founder review** (2026-08-02)

Branch `feature/mvp-060-mediation-proxy` (off main). *"The only path from model to tools — enforces manifests, params, rates, budgets, tiers, and audit, in that order."* **No migration, no new dependency.** `core/mediation/proxy.py` — `call(ctx, tool, params)` runs the authoritative ordered chain: **manifest integrity → grant → untrusted-narrowing → param constraints (jsonschema) → rate limit (redis) → budgets → tier (approval) → audit intent (log-then-act) → execute → egress scrub**. A denial returns a **structured recoverable `ToolError`** (never the manifest contents); **≥3 manifest violations abort the run** (`RunAborted`) and each denial is **audited + alerted** (`alert.ops`). `core/mediation/tools.py` — registry: `catalog.search`/`pricing.compute`/`ledger.read` wired; `messages.send` reachable only past a **tier-2 approval** (so it never fires unapproved); `calendar.book`/`crm.*` gated stubs (`provider_unavailable`). `scripts/guards.py` — new **`runtime-not-tools`** lint guard (the ticket's import-linter contract, implemented via the existing AST-guard pattern — no new dep): `core/runtime/` may reach tools only through `core.mediation.proxy`. **419 pytest** (+11), incl. both ACs (out-of-manifest denied+audited+alerted; ≥3 abort) + full check-order coverage. **Out of scope (per ticket):** the live policy engine (tier stubbed conservative-2 until MVP-065). **Deferred (disclosed):** ed25519 manifest **signature** verify (hash integrity checked now); real PII **egress** scrub (pass-through hook); scalar policy constraints (recorded, enforced by the policy engine later); **wiring the executor's `tool_call` node through the proxy** — the ApprovalPending/abort handling belongs with the approvals engine (MVP-065), and the guard already enforces the boundary structurally.

## MVP-055 · Executor skeleton + checkpoints — **Completed — awaiting founder review** (2026-08-02)

Branch `feature/mvp-055-executor` (off main). *"Conversation state survives any crash and resumes without duplicate effects."* **Founder-approved:** LangGraph adopted (`langgraph==0.2.76`, MIT, +17 transitive incl. `langchain-core`/`langgraph-checkpoint` — DECISIONS 2026-08-02) and the runtime migration lands ahead of approvals-014. **Migration 015** (`f124e1102952`, chains off 013): `model_routes` (global), `agent_runs`/`agent_steps`/`agent_memory` (+RLS); `agent_runs` carries **`composed_prompt_hash` + `permission_manifest_hash` NOT NULL** (the AC); round-tripped, roles re-applied. `core/runtime/graph.py`: the LangGraph `StateGraph` **route→compose→model_turn→(tool_call↔)respond**, bounded tool loop; the same node fns + branch drive the durable executor so the declared graph and the driver never diverge. `core/runtime/executor.py`: runs the graph one node at a time with a **durable checkpoint after every node** (Redis snapshot + `agent_steps` row, `UNIQUE(run_id,seq)`), per-step **kill-switch** (flag, fail-closed) + **budget** (instance step cap) + **timeout**; `respond`'s effect is **idempotent on the run id** so a replay never double-sends. `core/runtime/model.py`: gated-simulated provider-agnostic `SimulatedModel` (real `RealModel` fails closed on `llm_provider_enabled`). `GET /v1/ops/runs/{id}` (`PLATFORM_ADMIN`, RLS-scoped). **408 pytest** (+12), incl. the headline **chaos-kill 10/10 resume with no duplicate send** (crash injected at model_turn/tool_call/respond/after-effect), checkpoint-conflict idempotency, tenant isolation, both-hashes-recorded. **Mediation excluded → MVP-060.** **Deferred:** real LLM provider (go-live), true LangGraph durable-saver (the executor owns durable checkpointing instead — disclosed), scheduler/worker wiring of runs.

## MVP-051 · Rate ingestion + manual entry — **Completed — awaiting founder review** (2026-08-02)

Branch `feature/mvp-051-rate-ingestion` (off main). *"Fresh IBJA rates or a fail-closed refusal — never a guess."* **No migration** (rate_sources/rate_snapshots exist in 013). `core/pricing/rates.py`: `ingest_rate` writes a `rate_snapshots` row unless the new value jumps more than the source's `max_step_pct` vs the last good rate → **quarantined** (not written, so the staleness clock keeps ticking on the last good rate); `fetch_and_store` runs the fetch, and on quarantine raises **`alert.ops`** (global raw-stream publish, the DLQ-alert precedent), on success publishes `rate.updated`; `record_manual_rate` (the launch hedge) writes an owner-entered rate, audited (keys only — never the values). **Gated-simulated**: default `SimulatedRateFetcher` (no external call); the real `HttpRateFetcher` fails closed until `rates_provider_enabled` + a chosen IBJA endpoint (BLOCKERS #5). `POST /v1/rates/manual` (`requires(ORG_MANAGE)`, audited). Staleness→409 is enforced by the existing engine gate (`_fresh_rate_lookup`): ≤24h fresh, >24h → `stale_rate`. **396 pytest** (+10: bounds math/boundary, fetcher gating, quarantine+alert, updated event, 23h-ok/25h-stale boundary, manual entry audited). **Deferred:** real tier-2 **approval** gate (approvals engine is MVP-065 — today: owner permission + audit); scheduler firing of the fetch job (scheduler entrypoint is still the MVP-028 placeholder, cf. #16); the org fan-out of `rate.updated`/`rate.stale`.

## MVP-049 · Availability + price-input staleness — **Completed — awaiting founder review** (2026-08-02)

Branch `feature/mvp-049-availability-stale` (off main). **No migration** (`quotes.stale_inputs` already exists in 013). `core/catalog/availability.py` (Rule-Zero-clean — no industry noun): (1) **availability transitions** — a validated state graph over `catalog_items.availability` (`in_stock`/`made_to_order`/`out`; `bookable_slot` is clinic/out-of-scope, so never a source or target); `transition(...)` updates + appends `catalog.availability_changed` to the org's **audit chain** (an agent-actor change is attributable). (2) **price-input staleness** — `price_input_deps(strategy)` walks each stage's **rule AST** (`ast` over the engine's `to_python`) to derive exactly the catalog inputs the rules read (jewelry → `{net_weight_g, purity, stones, requested_discount_minor}`), so nothing is hard-coded. `flag_quotes_if_price_inputs_changed(...)` (wired into `catalog.crud.update_item`) flags open (draft, unexpired) quotes computed from an item **only when a rule-referenced attribute changed** — a quote references an item via its stored `inputs.item_id` (linkage decision: no quote→item FK exists per ER E3; disclosed). Editing weight flags the dependent quote; editing gender flags nothing. **The typed `catalog.price_inputs_changed` event is deferred** — its payload schema lives in the vault's read-only `topics.yaml` (BLOCKERS #17); the flag (the MVP signal) is written synchronously. **386 pytest** (+8: per-stage AST extractor + pack-agnostic + transition graph/audit + open/referencing-only flagging + weight-vs-unrelated edit).

## MVP-054 · Send-path figure check — **Completed — awaiting founder review** (2026-08-02)

Branch `feature/mvp-054-send-figure-check` (off main). **The last line of defence: no unledgered rupee amount leaves the building.** `core/pricing/extract.py` — pure-Python (no I/O, no model) money extractor: parses ₹/Rs/Rs./INR, lakh/lac/crore/cr/k/thousand words, Indian lakh grouping (`1,00,000`), and paise, all via `Decimal` (never a float). **Conservative** to hold the <0.5% false-positive bar — a run of digits is money only with a currency marker, a magnitude word, or unmistakable Indian grouping, so a phone number / order id / `8 pm` is not a figure. `core/channels/whatsapp/send.py` **Gate 5** (after suppression+consent, before any Meta call): every amount in `body` must match an unexpired ledger row (`ledger.match`, exact) — an unmatched figure raises `unledgered_figure` (**422**, canonical) and never touches the wire; `figure_check=block|warn|off` (warn W2 → block W3 via flag) and a tier-3 `figure_override_by` owner lets it proceed but records the override on the **audit chain** (count only — amounts never logged/audited). **No migration** (reads the 053 ledger). **378 pytest** (+21: mt-* trap corpus incl. Indian formats/word-amounts/negatives + send-path allow/block/partial/warn/override). Existing 4 gates + send tests unaffected.

## MVP-052 + MVP-053 · Quote service/API + committed-figures ledger — **Completed — awaiting founder review** (2026-08-02)

Branch `feature/mvp-052-quotes-api` (off main). **No migration** (013 already created `quotes` + `committed_figures_ledger`). `core/pricing/service.py`: `compute_quote` resolves the strategy, **pre-loads the pack's freshest in-window snapshot per source** (so the sync engine gets a sync rate lookup — no async inside `compute`), runs the engine, and writes the `quotes` row **and** its ledger rows in **one transaction** (atomicity proven by a monkeypatched ledger failure leaving zero quotes); `replay_quote` reloads stored inputs+params, **pins the exact `rate_snapshot_ids`** the quote used, recomputes, and reports a **byte-for-byte** breakdown+total match; `rates_status` for freshness. `core/pricing/ledger.py` (MVP-053): `write` records the total + every positive breakdown line with expiry; `match` is **exact (tolerance 0)**, unexpired, within a 48h window — an off-by-one or expired figure fails closed (the MVP-054 send-gate input). `core/pricing/api.py`: `POST /v1/pricing/compute` (409 `stale_rate` / 422), `POST /v1/pricing/replay`, `GET /v1/rates/status`, all `requires(CATALOG_READ)`. `registry.get_strategy` now returns the full strategy dict so lookups rebuild at compute time. **357 pytest** (+6: provenance, every-figure-matchable, byte-exact replay, atomic-on-ledger-failure, stale fail-closed, expired-no-match). Unlocks 054; 049 next.

## MVP-034 · Gated send adapter — **Completed — awaiting founder review** (2026-07-30)

Branch `feature/mvp-034-036-send-adapter` (off main). `core/channels/whatsapp/send.py`: the single outbound exit, with four fail-closed gates before any Meta call — (1) **audit capability** (fresh <10min entry authorising this exact `msg.send` → `approval_required`), (2) **execution token** (stub, non-empty required; real one-time binding lands MVP-066 → `approval_required`), (3) **suppression** (marketing/all scope → `suppressed_contact`; lookup error fails closed), (4) **consent** (marketing needs positive consent; transactional exempt → `consent_missing`). On success: outbound `messages` row + `msg.sent.v1` + audit `msg.send:succeeded`; on exhausted failure: `status=failed` + `msg.failed.v1` + `msg.send:failed`. Retries honour 429 Retry-After and retry 5xx ×3 (bounded). Meta stays gated-simulated. **No migration** (schema already had `contacts.consent_status`, `suppressions`, `messages.audit_id`). **191 pytest, 0 skipped.**

**MVP-036 enforcement folded in here** (the suppression+consent join is the same gate).

## MVP-050 · Pricing migration + rules_v1 engine — **Completed — awaiting founder review** (2026-08-02)

Branch `feature/mvp-050-pricing-engine`. Migration `63bcec3ea528` (013: pricing_strategies/rate_sources/rate_snapshots global; pricing_rules/quotes/committed_figures_ledger +RLS). `core/pricing/`: `functions.py` (Decimal round/sum/min/max, TaxRule, DotItem, float-reject), `engine.py` (**safe AST interpreter** + DSL preprocessing → exact per-stage compute, residue fail-closed, provenance, stale_rate), `registry.py` (load/get strategy + source_for/tax_rules builders). **Both packs' goldens pass on the SAME engine, zero changes** (jewelry pg-001/002/031, kirana kpg-01/02/04). pg-014 sample flagged as formula-inconsistent (DECISIONS). **351 pytest.** Unlocks 052/053/049/054.

---

## MVP-059 · Composer + tenant layer generator — Completed — merged to main `7a2e0ff` (2026-08-01)

Branch `feature/mvp-059-composer`. `core/prompts/composer.py`: `render(binding)` composes base+vertical+tenant per prompt-registry.md — immutable per-version layer cache (fail-closed `LayerMissing`), strict `render_template` (missing `{param}` → `MissingParam`, run refuses to start), `check_compat` on `requires{}`, sha256 `content_hash` reproducible across processes. `core/prompts/tenant_layer.py`: `generate_tenant_layer` bakes settings (persona/store/policies/language) into template v1, hash-versioned (idempotent; regenerates on settings change); `resolve_tenant_facts` with defaults. `prompts/base/concierge.md` (base.concierge@1.0, industry-agnostic hard rules). **336 pytest.**

---

## MVP-042 · Catalog schema registration + index gen — Completed — merged to main `0cee988` (2026-08-01)

Branch `feature/mvp-042-index-gen`. Migration `1b9dc38df16c` (`catalog_schemas.generated_ddl` text[]). `core/packs/indexes.py`: `generate_index_ddl` (partial expression indexes from `x-index`/`x-index-type` — scalar btree, numeric typed-cast, array GIN; deterministic), wired into the installer's schema registration; `apply_generated_indexes` applies them CONCURRENTLY (autocommit migrator conn, `lock_timeout=3s`, IF NOT EXISTS → contended ones deferred to next run). Jewelry generates 6 indexes (the ticket's "three" predates the added weight/gender/occasion x-index attrs; DDL snapshot is the verbatim check). **328 pytest.** Catalog subsystem now complete except 049 (needs quotes/050).

---

## MVP-048 · Embeddings + hybrid RRF — Completed — merged to main `308c578` (2026-07-31)

Branch `feature/mvp-048-embeddings-rrf`. `core/catalog/embed.py`: pluggable `Embedder` — default **SimulatedEmbedder** (deterministic 1024-dim, no paid API; real provider gated behind `embeddings_provider_enabled`, fails closed until picked — BLOCKERS #16); `embed_pending` batch + `run_embeddings_batch` (per-org) + `register_jobs`. `core/catalog/search.py`: `rrf_fuse` (k=60), kNN via pgvector `<=>` (HNSW), `hybrid_search` (BM25 ⊕ kNN, filter pushdown, empty→nearest = 3 closest). `GET /v1/catalog/search` now returns fused `{results, nearest}` + attribute `filters`. **323 pytest.** Real semantic quality awaits the provider; the mechanics (fusion/nearest) are tested.

---

## MVP-047 · Text search (BM25) — Completed — direct commit to main `9c78ceb` (2026-07-31)

Branch `feature/mvp-047-text-search`. `core/catalog/search.py`: `search_text` tsvector built from title + description + the pack's `x-search` projected attributes (`simple` ⊕ `english` configs so exact tokens like "22k" and vernacular aliases match alongside stemmed English), maintained on every write via `search.refresh` (wired into CRUD create/update); `search_items` ranks with `ts_rank` over the GIN index. `GET /v1/catalog/search?q=&k=` returns `{results, nearest:[]}` (nearest filled by MVP-048). **318 pytest.**

---

## MVP-046 · Attributes validation (JSON Schema + CEL) — Completed — merged to main `63cb525` (2026-07-31)

Branch `feature/mvp-046-attr-validation`. `core/catalog/validate.py`: Draft 2020-12 validation (with `additionalProperties:false` → unknown attrs rejected) + `constraints` CEL eval (celpy) → `{path, error, rule}` problems; compiled validators + CEL programs cached per (pack, version). Wired into `crud.create_item`/`update_item` (→ `ValidationProblems` → **422** with path detail). `jsonschema` made an explicit dep. **315 pytest, 0 skipped** (7 validation unit + wiring). Also this session: restored the `docs/` vault symlink after it was replaced by a stray dir (BLOCKERS #15, resolved).

---

## MVP-045 · Catalog migration + CRUD — Completed — merged to main `170eec0` (2026-07-31)

Branch `feature/mvp-045-catalog-crud`. Migration `d2cecc53f63c` (012): `catalog_items` (+RLS, GIN(search_text), HNSW(embedding vector(1024))), `catalog_items_history` (+RLS, snapshot + actor/reason), `catalog_idempotency` (+RLS); `CREATE EXTENSION vector`. `core/catalog/crud.py`: create/get/list(keyset cursor)/update(If-Match)/soft-delete, each writing a history row; identity-key dedup (→ `DuplicateIdentity` with existing id), `Idempotency-Key` replay, pack+schema_ver resolved from the active install. `core/catalog/router.py`: `POST/GET/PATCH/DELETE /v1/catalog/items(/{id})` (409 duplicate, 412 If-Match, cursor). Deep attribute validation → MVP-046. **Unblocks MVP-042** (catalog_items now exists). **307 pytest, 0 skipped.**

---

## MVP-043 · Kirana dry-run CI gate — Completed — merged to main `b70a4b1` (2026-07-31)

Branch `feature/mvp-043-kirana-dryrun`. Added `installer.dry_run(org, pack_dir)` — runs the **full** install pipeline inside a transaction that is always rolled back, returning an `InstallPlan` (validates contracts, exercises every step, persists nothing). `verticals/kirana/install.yaml` (expected_plan) + `tests/e2e/test_kirana_dryrun.py` (plan matches: 3 bindings/instances, 5 layers, schema v1, 2 workflows/integrations; **zero rows persisted**). CI `migrate` job now runs the kirana dry-run beside the jewelry e2e. Proves "second pack installs with zero core changes" — a jewelry hardcode in core would make it red. **291 pytest, 0 skipped.**

---

## MVP-041 · Jewelry install e2e fixture — Completed — merged to main `527412a` (2026-07-31)

Branch `feature/mvp-041-jewelry-install-e2e`. `verticals/jewelry/install.yaml` (reference install: config slot values + `expected_result`) + `tests/e2e/test_jewelry_install.py` (fresh org → install → asserts status=active, 4 paused instances, catalog schema v2, 9 candidate prompt layers, 4 bindings, deferred steps, <60s). Wired into CI (`migrate` job creates `app_rw` then runs the e2e — permanent required check). No production code. Index-queued assertion pending MVP-042. **290 pytest, 0 skipped.**

---

## MVP-040 · Transactional installer + API — Completed — merged to main `9fa3ac3` (2026-07-31)

Branch `feature/mvp-040-installer`. `core/packs/installer.py`: 6-step transactional install pipeline (single tenant-scoped txn) — catalog schema → pack-migrations(none) → prompt layers(candidate) → **policies(deferred)** → **workflows(deferred)** → bindings + paused instances; **digest idempotency** (reinstall = no-op), **rollback** (failure at any step → zero partial rows + install marked `failed` at that step), **uninstall** (re-pause instances, retain schema, L3 untouched). Status machine `installing→active/failed/uninstalled`; migration `5dcbda42efca` adds `failed` to the CHECK. API `GET /v1/packs`, `POST /v1/packs/installations`, `DELETE …/{id}` ([router.py](core/packs/router.py)). Policies/workflows seeding + attribute-freeze deferred to when 012/014/016 land (BLOCKERS #14; founder decision 2026-07-31). **289 pytest, 0 skipped.**

---

## MVP-039 · Bundle parser + verifier — Completed — merged to main `db28412` (2026-07-31)

Branch `feature/mvp-039-bundle-parser`. `core/packs/bundle.py`: `split_prompt_layers` (anchor `.md` → `PromptLayerDef` records, version from header — concierge.md → 4 layers), `parse_pack_dir` (validate every file via the MVP-038 contracts, **path-precise + file-named** errors), digest manifest (`compute/verify_manifest` — tampered file refused) + **ed25519** `verify_signature`, and `load_bundle` (dev = directory; prod `packs_dev_mode=False` requires matching MANIFEST + valid signature). No new deps (ed25519 via cryptography). `.tar.zst` transport deferred (needs `zstandard` — BLOCKERS #13; verification is over the tree, so no criterion affected). **279 pytest, 0 skipped.**

---

## MVP-038 · Pack contract models — Completed — merged to main `9cda64f` (2026-07-31)

Branch `feature/mvp-038-pack-contracts`. `core/packs/contracts.py` (pure pydantic, zero I/O): typed L0↔L1 contracts per [core-platform.md](docs/21-platform/core-platform.md) — `PackManifest` (with `slots`), `AgentBinding`/`TaskDef`/`ToolGrant`/`PolicyRuleRef`, `CatalogSchema` (`from_document` split), `PricingStrategyDef`, `WorkflowDef`, `IntegrationSpec`, plus auxiliary `OnboardingPack`/`UiPack`/`CalendarPack`/`EvalSuite` (full scope, founder 2026-07-31). **Strict** (`extra=forbid`) where the platform owns the shape, **open** where the pack/engine does. Models the pack **data** where it deviates from the illustrative spec signatures (DECISIONS 2026-07-31). **Every** verticals/* contract file parses (both packs) + path-precise negative fixtures. **266 pytest, 0 skipped.** Prompt `.md` anchor-splitting → MVP-039.

---

## MVP-037 · WhatsApp media handling — Completed — merged to main `b0a3bd0` (2026-07-31)

Branch `feature/mvp-037-media`. `core/channels/whatsapp/media.py`: inbound media pipeline — **mime allowlist** + **size cap** gates, gated Meta download, **fail-closed AV scan** (scanner error → quarantine + `alert.ops.v1`; infected → rejected), object store, descriptor written to `messages.media`; plus an outbound `upload_outbound_media` helper. Scanner + store are **pluggable, simulated by default** (no new deps, §9 founder-approved 2026-07-31); real clamav/MinIO gated behind `media_av_enabled`/`media_storage_enabled` which **fail closed** until wired (BLOCKERS #12). Normalizer downloads/scans/stores media and links it; a disallowed mime still normalizes (text fallback). `meta_client` gained gated `download_media`/`upload_media`. **225 pytest, 0 skipped.** Deferred: real clamav+MinIO deps + compose (#12); media rendering in transcript (frontend); Meta media I/O gated (#3).

**🎯 The WhatsApp channel group (031–037) is complete** — connect, ingress, normalize, send (4 gates), templates, opt-out compliance, media — all merged to main except 037 (this branch).

---

## MVP-035 · WhatsApp templates management — Completed — merged to main `1eb7f5f` (2026-07-31)

Branch `feature/mvp-035-templates`. `core/channels/whatsapp/templates.py`: registry CRUD (`upsert`/`list`/`get`), gated `submit_template` (→ Meta review, simulated), `apply_status_update` (Meta `message_template_status_update` events → our status + rejection reason), `assert_template_sendable` (non-approved → `TemplateNotSendable` naming the template), `process_template_status_pending` drainer (resolves org by WABA id, RLS-exempt), and `seed_from_manifest`. Migration `83efabba79ee` adds Meta-sync columns to `message_templates`, `channels.waba_id`, and `resolve_channel_by_waba`. Wired: `send()` gains a `template=(key,lang)` path (gate + `send_template`); `connect.py` populates `waba_id` (touches MVP-031, DECISIONS 2026-07-31); normalizer skips status webhooks; `GET /v1/channels/whatsapp/templates` (owner). jewelry_v2 seed declared in `verticals/jewelry/templates/whatsapp.yaml` + `scripts/seed_whatsapp_templates.py` (gated). **214 pytest, 0 skipped.** Deferred: template-builder UI + campaign-wizard picker (frontend, MVP-08x); real Meta submission/webhooks gated (#3).

---

## MVP-036 · Opt-out keyword net — Completed — merged to main `9e3a11d` (2026-07-30)

Branch `feature/mvp-036-stop-keywords`. `core/channels/whatsapp/keywords.py` (STOP/UNSUB net — English + romanised Hindi `band karo` + Telugu `ఆపండి`, whole-message strict match; ASCII-only punctuation strip so non-Latin marks survive) wired into the normalizer: a STOP inbound auto-suppresses the contact (`scope=marketing`, idempotent PK) and, **on the first suppression only**, sends the fixed transactional confirmation through the gated send adapter *after the event commits* (durable suppression first). The confirm mints its own audit capability, so it passes all MVP-034 gates. Founder-approved automated send — DECISIONS 2026-07-30. **206 pytest, 0 skipped.** Remaining 036: suppressed badge in chats (frontend, lands with chats page MVP-087).

---

## MVP-031 · WhatsApp WABA connect — Completed — merged to main `644b334` (2026-07-30)

Branch `feature/mvp-031-whatsapp-connect` (off main). The "owner connects their WhatsApp number" step: `POST /v1/channels/whatsapp/connect` runs three gates (token → handshake → echo, all **simulated** until `whatsapp_live_enabled`, §10.4 / BLOCKERS #3); on full success it writes a `channels` row (active) + a **Fernet-encrypted** credential in the new `channel_credentials` table (org-scoped, RLS). Reconnect updates in place; a number owned by another org → 409; `GET /.../{id}/health` re-runs the echo probe. Migration `cfd462c65ec9` (round-trip verified). No new deps (cryptography already present via python-jose). **183 pytest, 0 skipped.** Awaiting founder review → commit. Next candidate: MVP-034 (gated send adapter).

---

## Active (prior): MVP-012–030 batch — COMPLETE (19/19). 026–030 done on branch (awaiting commit)

**🎯 The MVP-012..030 goal is complete — 19/19 tickets, all verified live.** On main: 012–020 + 024/025 + 021/022 + 058/023 (`25527c0`). On branch `feature/mvp-026-030-consumers` (uncommitted): MVP-026/027/028/029/030 (consumer framework, dedupe, scheduler, retries/DLQ, typed event catalog) — **168 pytest**, migrations linear through 011, RLS enforced. No new deps, no new migrations. Awaiting commit → main.

**What's live:** the full platform foundation — auth/sessions/RBAC, tenant isolation (RLS enforced under `app_rw`), API keys, invites, messaging + CRM + prompt schemas, audit hash-chain, transactional outbox, packs+archetypes, tenant settings + feature flags, and the Redis-streams consumer/scheduler/DLQ + typed event bus. Next: founder selects post-30 work (catalog/pricing/agent-runtime/WhatsApp channel per the roadmap).

**On main:** 012–019 (`290c476`) + 024/025/020 (`dbab65a`/`2aeb288`) — migrations linear through 008, RLS enforced. **13/19 of the 012–30 goal done.** Next: MVP-021 + MVP-022 (tenant settings + feature flags, migration 009), then 058 (010), 023 (011); then the Redis-streams consumer set 026–029 + 030.


**Pushed to main:** 012–015 (`35457ef`/`8cfa3e8`) and 016–019 (`290c476`/`a139ac3`). RLS is enforced (app runs as `app_rw`). Next: **MVP-024** (audit hash-chain, migration 006) → MVP-025 (events/007) → MVP-020 (packs/008).


**Status:** 012–015 committed `35457ef`, **merged to main `8cfa3e8`/`7b769da` and pushed**. **MVP-016 implemented 2026-07-29 on `feature/mvp-016-tenant-middleware`** (off main) — **not yet committed**. Batch continues **ticket-by-ticket through 012–030** (implement → verify live → log each; stop only on a new decision/blocker). Environment live: postgres+redis healthy, **107 pytest (0 skipped)**, RLS now enforced. Migration order 002→011; migration **010 (prompts, MVP-058) pulled forward** before CRM (011/MVP-023).

**Done + verified this session:**
- **MVP-012** ✅ refresh rotation + reuse-revokes-family + rotation-race; `jti` nonce fix. (`35457ef`, main)
- **MVP-013** ✅ logout + logout-all. (`35457ef`, main)
- **MVP-014** ✅ migration 002 (orgs + user_orgs +RLS + `app.user_id` self-policy); `POST /v1/orgs`, `GET /v1/me`; `apply_rls` NULLIF (ratified). (`35457ef`, main)
- **MVP-015** ✅ RBAC migration 003 + `@requires` + 403 problem+json. (`35457ef`, main)
- **MVP-016** ✅ `app_rw` non-BYPASSRLS role + 2-URL split; `get_db`/`org_scoped_session` SET LOCAL; session-SET guard. **RLS now ENFORCED — BLOCKERS #11 RESOLVED.** Verified live end-to-end (isolation tests + uvicorn smoke). Awaiting commit.

- **MVP-018** ✅ api_keys (migration 004 +RLS + `resolve_api_key` SECURITY DEFINER); `require_key_scope`; founder-only issuance.
- **MVP-019** ✅ messaging migration 005 — 6 org-scoped +RLS; `webhook_events` global (DECISIONS 2026-07-30).
- **MVP-017** ✅ `invites` (global, appended after 005); owner-invite + accept-as-staff; `invites_enabled` gate.

**MVP-020 deferred** behind MVP-024 (audit/006) + MVP-025 (events/007) to keep the alembic chain linear (founder decision 2026-07-29).

**Next:** commit + push MVP-016–019 to main; then MVP-024 (audit hash-chain, migration 006) → MVP-025 → MVP-020.

**Authoritative docs:** `docs/tickets/MVP-0NN.md`; `docs/25-implementation-starter-kit/13-auth-rbac-approval-audit.md`; `docs/25-implementation-starter-kit/09-database-migration-order.md`; `docs/21-platform/multi-tenant-rls.md`.

---

## Prior: MVP-006 – MVP-010 · platform foundations batch (merged)

**Status:** Completed — implemented 2026-07-22, committed `684a000`, merged `e128c11`. Outcome: **007 + 010 DONE**, **006 + 008 PARTIAL**, **009 BLOCKED (scaffold only)** — see [MVP_STATUS.md](MVP_STATUS.md) + [IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md); MVP-009 blocker in [BLOCKERS.md](BLOCKERS.md) #10.

---

## Prior: MVP-011 · OTP auth endpoints (merged to main `eeab4e2`)

**Objective:** As a store owner, sign in with phone + OTP (no passwords). Implement `POST /v1/auth/otp` and `POST /v1/auth/otp/verify` per the auth spec: hashed codes, 5-minute expiry, ≤5 attempts, 60s resend throttle, dev-mode code log behind a flag. Verify issues a server-side session row + JWT (15m access / 30d refresh rotation, claims `sub, org_id, roles[]`).

**Status:** Completed — awaiting founder review. Implemented 2026-07-22 on branch `feature/mvp-011-otp-auth` (see [IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md)), then amended the same day to an **interim email OTP channel** (phone kept behind `GROWTH_OPERATOR_OTP_CHANNEL`; Meta deferred — see [DECISIONS.md](DECISIONS.md) and [TODO.md](TODO.md)). All static/unit gates pass (ruff, mypy, **37 pytest**). Live-DB acceptance and real-email staging delivery remain BLOCKED (no Docker this session — BLOCKERS #2; real email provider still needed — TODO #2). Do not select the next ticket until the founder reviews and explicitly chooses it.

**Branch:** `feature/mvp-011-otp-auth`.

**Authoritative docs:**
- `docs/tickets/MVP-011.md` (the ticket itself)
- `docs/25-implementation-starter-kit/13-auth-rbac-approval-audit.md` (Auth section — OTP shape, JWT claims, session model)
- `docs/25-implementation-starter-kit/09-database-migration-order.md` (migration 001: `users, sessions, otp_challenges`)
- `docs/21-platform/multi-tenant-rls.md` (RLS pattern — n/a for this ticket per MVP-011 scope note: users/sessions are global, not org-scoped)
- `docs/implementation/db/migrations/README.md` (migration rules: lock_timeout, expand/contract, RLS-in-same-migration)

**Acceptance criteria (from MVP-011):**
- [x] Brute force locked after 5 attempts (unit-tested + verified live via `tests/integration/test_auth_flow.py::test_lockout_after_five_attempts`)
- [x] Resend throttled to 60s (unit-tested; live DB up)
- [ ] OTP delivered to founder's real inbox in staging — **interim:** now "real **email** in staging" (Meta pending API access, TODO #1). Local end-to-end verified against real Postgres; real-inbox delivery still needs an email provider (TODO #2) + a deployed staging env

**Test cases (from MVP-011):**
- [ ] Expiry boundary (5m)
- [ ] Attempt lockout (≤5)
- [ ] E.164 phone validation

**Expected files:**
- `migrations/versions/001_identity.py` (or ruff-generated slug) — `users`, `sessions`, `otp_challenges` tables
- `core/tenancy/auth.py` — challenge create/verify, argon2 hashing, Redis-backed throttle, dev-mode code logging behind a flag
- `core/api/` — router wiring for `POST /v1/auth/otp`, `POST /v1/auth/otp/verify`
- Tests under `tests/unit/` and/or `tests/integration/` for the three test cases above

**Commands to run:**
```bash
uv run alembic revision -m "001_identity"
uv run alembic upgrade head          # requires live Postgres — see BLOCKERS.md #1
uv run pytest -v
uv run ruff check .
uv run mypy core
```

**Blockers:** `BLOCKERS.md` #1 and #2 must be resolved or explicitly waived
before live database verification:

- #1: Docker Compose environment-variable prefix mismatch.
- #2: Docker stack and Alembic migration path have not yet been verified locally.

**Next prompt:** "Implement MVP-011 (OTP auth endpoints) per `docs/tickets/MVP-011.md` and `docs/25-implementation-starter-kit/13-auth-rbac-approval-audit.md`. Write migration 001 (users, sessions, otp_challenges) with `migrations/lib/rls.py` applied where applicable, then `core/tenancy/auth.py` and the two API routes. Add tests for expiry boundary, attempt lockout, and E.164 validation."
