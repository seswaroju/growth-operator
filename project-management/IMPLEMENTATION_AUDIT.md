# Growth Operator Implementation Audit

## Audit metadata

- **Date:** 2026-07-10
- **Git branch:** main
- **Current commit:** no commits yet
- **Claude session name:** not exposed to this agent (no session identifier available in-environment)
- **Repository path:** /Users/srila/AI-Growth-Operator/growth-operator

## Executive summary

**Actually implemented:**
- Monorepo scaffold: full `core/` module tree, `web/` Vite+React+TS+Tailwind+TanStack Query shell, `migrations/` Alembic (async) framework, `infra/docker/` compose stack, `.github/workflows/ci.yml`, `Makefile`.
- `core/common/errors.py` — RFC7807 Problem model + all 12 canonical error codes, wired into the FastAPI app.
- `core/common/config.py` — layered pydantic-settings (env > .env > SOPS-decrypted file > defaults).
- `migrations/lib/rls.py` — `apply_rls()` / `drop_rls()` helper implementing the exact multi-tenant RLS SQL pattern.
- `verticals/jewelry/` and `verticals/kirana/` — complete declarative pack data (manifests, catalog schemas, pricing strategies, workflows, prompts, evals, onboarding, UI templates), copied in whole from the doc vault.

**Partial:**
- `core/api/main.py` — FastAPI app object exists with exception handling wired, but zero routes.
- `infra/docker/docker-compose.dev.yml` — fully written but never run against a live Docker daemon in this environment, and contains an env-var naming bug (see Known risks / Blockers).
- `tests/unit/test_scaffold.py` — covers only "does every core module import" and "do Makefile targets exist"; no feature-level tests exist anywhere.

**Placeholder-only (single-line docstring, zero logic):**
`core/tenancy`, `core/audit`, `core/events`, `core/approvals`, `core/catalog`, `core/pricing`, `core/channels` (+ `whatsapp`), `core/ingestion`, `core/prompts`, `core/runtime`, `core/workflows`, `core/insights`, `core/packs`, `core/mediation`.

**Broken:** Nothing is broken in the sense of failing to import/build — ruff, mypy, pytest, and the frontend `tsc`/build are all clean. One **latent runtime bug** exists (Docker Compose env var prefix mismatch, see below) that will surface the first time any service actually opens a DB/Redis connection inside a container.

**Estimated MVP completion:** ~3–4% of the 100-ticket MVP scope. MVP-001 (scaffold) is fully closed; MVP-002 (docker-compose), MVP-003 (CI), MVP-004 (migration framework + RLS helper), and MVP-005 (config + error taxonomy) are functionally present but not fully verified/tested per their own acceptance criteria. MVP-006 through MVP-100 are untouched. Zero database tables exist; zero API endpoints exist; zero frontend pages exist.

## Module-by-module status

### API (`core/api`)
- **Status:** partial
- **File paths:** `core/api/__init__.py`, `core/api/main.py`
- **Public interfaces:** `core.api.main:app` (ASGI app object); no route handlers; FastAPI's built-in `/openapi.json`, `/docs` only
- **DB tables/migrations:** none
- **Existing tests:** none dedicated (only the generic import-clean test touches it)
- **Missing tests:** endpoint tests (none exist because no endpoints exist); health/readiness endpoint tests (MVP-007)
- **Known risks:** none yet — nothing to break
- **MVP relevance:** entrypoint every future endpoint attaches to
- **Recommended next action:** leave as-is until MVP-011 adds the first real routes

### Authentication (no dedicated module — planned inside `core/tenancy`)
- **Status:** empty
- **File paths:** none exist; will live in `core/tenancy/` per `docs/25-implementation-starter-kit/06-backend-plan.md`
- **Public interfaces:** none
- **DB tables/migrations:** none — migration 001 (`users, sessions, otp_challenges`) speced, not written
- **Existing tests:** none
- **Missing tests:** OTP issuance/verify, session/JWT issue+refresh, revocation
- **Known risks:** `python-jose[cryptography]` installed but completely unused
- **MVP relevance:** critical path — blocks every authenticated flow
- **Recommended next action:** build now (MVP-011)

### Tenancy (`core/tenancy`)
- **Status:** placeholder
- **File paths:** `core/tenancy/__init__.py` (one-line docstring only)
- **Public interfaces:** none
- **DB tables/migrations:** none — migration 002 (`organizations, user_orgs`) speced, not written
- **Existing tests:** none
- **Missing tests:** `SET LOCAL app.org_id` middleware test, RLS isolation probes (org A cannot see org B)
- **Known risks:** none yet (no code)
- **MVP relevance:** critical — every org-scoped table depends on this middleware existing before RLS is meaningful
- **Recommended next action:** build now (MVP-014, MVP-016)

### RBAC (no dedicated module — planned inside `core/tenancy`)
- **Status:** empty
- **File paths:** none exist; will live in `core/tenancy/` per backend plan
- **Public interfaces:** none
- **DB tables/migrations:** none — migration 003 (`roles, permissions, role_permissions, user_roles`, seeded owner/staff/founder) speced, not written
- **Existing tests:** none
- **Missing tests:** role decorator enforcement test, seed data test
- **Known risks:** none yet
- **MVP relevance:** high — gates staff/owner permission boundaries
- **Recommended next action:** build after auth (MVP-015)

### Audit (`core/audit`)
- **Status:** placeholder
- **File paths:** `core/audit/__init__.py`
- **Public interfaces:** none
- **DB tables/migrations:** none — migration 006 (`audit_log` append-only + trigger, `dedupe_consumer`) speced, not written
- **Existing tests:** none
- **Missing tests:** hash-chain integrity test, "send without audit_id rejected" test
- **Known risks:** none yet; this is a hard platform invariant once other modules exist ("log-then-act")
- **MVP relevance:** critical — no send/execute API may ship without this
- **Recommended next action:** build immediately after tenancy (MVP-024)

### Events (`core/events`)
- **Status:** placeholder
- **File paths:** `core/events/__init__.py`
- **Public interfaces:** none
- **DB tables/migrations:** none — migration 007 (`event_outbox`) speced, not written
- **Existing tests:** none
- **Missing tests:** outbox emit test, consumer idempotency dedupe test, DLQ/retry test
- **Known risks:** `redis` package installed (covers Streams client), no other gap
- **MVP relevance:** critical — everything downstream fans out through this
- **Recommended next action:** build after tenancy (MVP-025..030)

### Approvals (`core/approvals`)
- **Status:** placeholder
- **File paths:** `core/approvals/__init__.py`
- **Public interfaces:** none
- **DB tables/migrations:** none — migration 014 (`approval_policies, approvals, trust_ledger, incident_tightening, execution_token_jti`) speced, not written
- **Existing tests:** none
- **Missing tests:** policy engine rule evaluation, execution token issuance/expiry
- **Known risks:** none yet; `cel-python` installed and sufficient for rule evaluation once built
- **MVP relevance:** critical — sole issuer of `execution_token` for tier≥1 actions
- **Recommended next action:** defer until packs exist (MVP-065, depends on MVP-044)

### Catalog (`core/catalog`)
- **Status:** placeholder
- **File paths:** `core/catalog/__init__.py`
- **Public interfaces:** none
- **DB tables/migrations:** none — migration 012 (`catalog_items` + history, embeddings HNSW+GIN) speced, not written
- **Existing tests:** none
- **Missing tests:** JSON-Schema attribute validation test, BM25/hybrid search test
- **Known risks:** **`jsonschema` is not in `pyproject.toml`** — required for Draft-2020-12 attribute validation (MVP-046); pgvector Python bindings also absent (needed MVP-048)
- **MVP relevance:** high — feeds pricing and search
- **Recommended next action:** defer until packs land (MVP-045, depends on MVP-042); add `jsonschema` dependency when started

### Pricing (`core/pricing`)
- **Status:** placeholder
- **File paths:** `core/pricing/__init__.py`
- **Public interfaces:** none
- **DB tables/migrations:** none — migration 013 (`pricing_strategies, pricing_rules, rate_sources, rate_snapshots, quotes, committed_figures_ledger`) speced, not written
- **Existing tests:** none
- **Missing tests:** stale-rate fail-closed test, committed-figures-ledger send-path check test
- **Known risks:** none yet; `cel-python` covers the rules_v1 engine
- **MVP relevance:** critical for jewelry vertical (rate-driven quoting) — invariant-heavy module
- **Recommended next action:** defer until MVP-022 (flags) lands (MVP-050)

### Channels (`core/channels`, `core/channels/whatsapp`)
- **Status:** placeholder
- **File paths:** `core/channels/__init__.py`, `core/channels/whatsapp/__init__.py`
- **Public interfaces:** none
- **DB tables/migrations:** none — migration 005 (`channels, contacts, conversations, messages, message_templates, suppressions, webhook_events`) speced, not written
- **Existing tests:** none
- **Missing tests:** webhook signature verification test, suppression fail-closed test, dedupe test
- **Known risks:** `httpx` installed (covers Graph API calls); no other gap
- **MVP relevance:** critical — sole MVP channel (A2); ingress/send/suppression gates all user-facing flow
- **Recommended next action:** defer until MVP-016/019, but the **Meta WABA verification prerequisite (external, non-code) should already be in flight** — longest lead-time item in the whole plan

### Ingestion (`core/ingestion`)
- **Status:** placeholder
- **File paths:** `core/ingestion/__init__.py`
- **Public interfaces:** none
- **DB tables/migrations:** none — migration 017 (`import_batches, import_rows`) speced, not written
- **Existing tests:** none
- **Missing tests:** extract/normalize/validate/review/load pipeline tests
- **Known risks:** none yet; photo-extraction OCR dependency not yet chosen (post-MVP-077, out of current scope)
- **MVP relevance:** medium — catalog bulk-load path, sequenced late (MVP-076..080)
- **Recommended next action:** defer

### Prompts (`core/prompts`)
- **Status:** placeholder
- **File paths:** `core/prompts/__init__.py`
- **Public interfaces:** none
- **DB tables/migrations:** none — migration 010 (`prompt_layers, prompt_bindings, prompt_evals`) speced, not written
- **Existing tests:** none
- **Missing tests:** registry versioning test, composer layer-merge test
- **Known risks:** none yet
- **MVP relevance:** high — feeds agent runtime prompt composition
- **Recommended next action:** defer until packs exist (MVP-058, depends on MVP-020)

### Agent runtime (`core/runtime`)
- **Status:** placeholder
- **File paths:** `core/runtime/__init__.py`
- **Public interfaces:** none
- **DB tables/migrations:** none — migration 015 (`agent_runs, agent_steps, agent_memory, model_routes`) speced, not written
- **Existing tests:** none
- **Missing tests:** executor checkpoint/resume test, planner routing test
- **Known risks:** **`langgraph` is named in `docs/25-implementation-starter-kit/06-backend-plan.md` as the stack choice but is absent from `pyproject.toml`** — must be added before this module starts
- **MVP relevance:** critical — executor/planner/checkpointing core (MVP-055..064)
- **Recommended next action:** defer — correctly far downstream of current gaps (auth/tenancy/events)

### Workflows (`core/workflows`)
- **Status:** placeholder
- **File paths:** `core/workflows/__init__.py`
- **Public interfaces:** none
- **DB tables/migrations:** none — migration 016 (`workflow_definitions, workflow_runs, workflow_run_events, wait_subscriptions`) speced, not written
- **Existing tests:** none
- **Missing tests:** DSL parser test, guard library test, wait/resume test
- **Known risks:** none yet; `pyyaml` + `cel-python` sufficient for DSL parsing/guards once built
- **MVP relevance:** high — jewelry automations (rate-alert hold, festival campaigns, silent-lead reactivation) depend on this
- **Recommended next action:** defer (MVP-072..075)

### Insights (`core/insights`)
- **Status:** placeholder
- **File paths:** `core/insights/__init__.py`
- **Public interfaces:** none
- **DB tables/migrations:** none — feeds off migration 018 (`campaigns_metrics`), not written
- **Existing tests:** none
- **Missing tests:** owner digest generation test, ROI metric calc test
- **Known risks:** none yet
- **MVP relevance:** medium — journey step 6 / ROI reporting, end of MVP sequence
- **Recommended next action:** defer (MVP-081, MVP-086)

### Jewelry vertical (`verticals/jewelry`)
- **Status:** complete as declarative data / placeholder for runtime support
- **File paths:** `verticals/jewelry/pack.yaml`, `verticals/jewelry/pack.md`, `verticals/jewelry/catalog/schema.json`, `verticals/jewelry/pricing/strategy.yaml`, `verticals/jewelry/workflows/*.yaml` (4 files), `verticals/jewelry/prompts/*.md` (5 files), `verticals/jewelry/evals/*.yaml` (5 files), `verticals/jewelry/onboarding/steps.yaml`, `verticals/jewelry/ui/templates.yaml`, `verticals/jewelry/calendar/events.yaml`, `verticals/jewelry/integrations/*.yaml` (4 files) — 27 files total
- **Public interfaces:** none (data only, no loader consumes it yet)
- **DB tables/migrations:** none — migration 008 (`packs, pack_installations, catalog_schemas, agent_archetypes, agent_bindings, agent_instances`) speced, not written
- **Existing tests:** none run — the pack ships its own eval suite files, but no harness executes them
- **Missing tests:** pack verifier/signature test, install e2e fixture (MVP-041)
- **Known risks:** `jsonschema` dependency absent (needed to validate `catalog/schema.json`); no `core/packs` loader exists to read any of this data yet
- **MVP relevance:** this is the actual product — Srila Jewellers is the pilot tenant
- **Recommended next action:** correctly deferred until MVP-038..044 (pack contracts/installer) exist

### Kirana vertical (`verticals/kirana`)
- **Status:** complete as declarative data / placeholder for runtime support
- **File paths:** `verticals/kirana/pack.yaml`, `verticals/kirana/pack.md`, `verticals/kirana/catalog/schema.json`, `verticals/kirana/pricing/strategy.yaml`, `verticals/kirana/workflows/*.yaml` (2 files), `verticals/kirana/prompts/*.md` (4 files), `verticals/kirana/evals/*.yaml` (2 files), `verticals/kirana/onboarding/steps.yaml`, `verticals/kirana/ui/templates.yaml`, `verticals/kirana/integrations/*.yaml` (2 files) — 16 files total
- **Public interfaces:** none
- **DB tables/migrations:** none (shares migration 008 with jewelry)
- **Existing tests:** none run
- **Missing tests:** same as jewelry — install e2e, dry-run CI gate (MVP-043)
- **Known risks:** same as jewelry (no loader exists)
- **MVP relevance:** architecture acceptance test only ("uninstall jewelry, install kirana, zero core changes") — not a pilot tenant
- **Recommended next action:** defer identically to jewelry

### Frontend (`web/`)
- **Status:** partial (scaffold only)
- **File paths:** `web/vite.config.ts`, `web/src/main.tsx`, `web/src/App.tsx`, `web/src/index.css`, `web/package.json`, `web/tsconfig*.json`, `web/index.html`
- **Public interfaces:** none — `App.tsx` is a one-line placeholder; no routes, no pages, no API client
- **DB tables/migrations:** n/a
- **Existing tests:** none — no test runner configured (no `vitest`/`playwright` in `package.json`)
- **Missing tests:** everything — no pages exist to test
- **Known risks:** TanStack Router installed (`@tanstack/react-router` in `web/package.json`) but not wired (no route tree, no `RouterProvider`); React 19.2.7 installed vs. React 18 specified in `docs/25-implementation-starter-kit/06-backend-plan.md`
- **MVP relevance:** needed from MVP-082 onward
- **Recommended next action:** defer until MVP-011 (auth endpoints) exists for pages to call

### Migrations (`migrations/`)
- **Status:** partial (framework complete, zero content)
- **File paths:** `migrations/env.py`, `migrations/script.py.mako`, `migrations/lib/rls.py`, `migrations/lib/__init__.py`, `migrations/versions/` (empty), `alembic.ini`
- **Public interfaces:** `apply_rls(table)`, `drop_rls(table)` in `migrations/lib/rls.py`
- **DB tables/migrations:** zero migrations exist; `alembic history` returns nothing; 18 migrations speced in `docs/25-implementation-starter-kit/09-database-migration-order.md` are all outstanding
- **Existing tests:** none migration-specific
- **Missing tests:** down-migration tests (required weekly per migration rules), RLS isolation test per table
- **Known risks:** `migrations/env.py` imports `core.common.config`, which resolves via `alembic.ini`'s `prepend_sys_path = .` — only works when `alembic` is invoked from the repo root (confirmed working, but fragile if invoked elsewhere)
- **MVP relevance:** critical — every module needing persistence is blocked on migration 001 existing
- **Recommended next action:** build now — write migration 001 (`users, sessions, otp_challenges`)

### Docker (`infra/docker/`)
- **Status:** partial (written, unverified)
- **File paths:** `infra/docker/docker-compose.dev.yml`, `infra/docker/Dockerfile.dev`, `infra/docker/Caddyfile.dev`
- **Public interfaces:** n/a
- **DB tables/migrations:** n/a
- **Existing tests:** none automated (no CI job runs `docker compose up`)
- **Missing tests:** container health end-to-end test
- **Known risks:** **`docker-compose.dev.yml` sets `DATABASE_URL`/`REDIS_URL` on `api`/`worker`/`scheduler`, but `core/common/config.py`'s `Settings` reads `GROWTH_OPERATOR_DATABASE_URL`/`GROWTH_OPERATOR_REDIS_URL` (env_prefix mismatch)** — containers will silently fall back to `Settings` defaults (`@localhost`) instead of the `postgres`/`redis` service hostnames, once any code path actually opens a connection. Never run against a live Docker daemon in this environment (daemon unreachable from this sandbox).
- **MVP relevance:** critical — blocks any real `make dev` run
- **Recommended next action:** fix the env var prefix before the first ticket that opens a live DB/Redis connection inside a container (MVP-011)

### Terraform (`infra/terraform/`)
- **Status:** empty
- **File paths:** `infra/terraform/` (directory exists, no files)
- **Public interfaces:** none
- **DB tables/migrations:** n/a
- **Existing tests:** none
- **Missing tests:** n/a
- **Known risks:** none — correctly out of scope this early
- **MVP relevance:** low until MVP-098/099 (Hetzner VPS, DNS, production infra)
- **Recommended next action:** defer, end of MVP sequence

### Test suites (`tests/`)
- **Status:** partial (one file, scaffold-level only)
- **File paths:** `tests/unit/test_scaffold.py`; `tests/contract/`, `tests/integration/`, `tests/isolation/`, `tests/e2e/` all exist as empty directories
- **Public interfaces:** n/a
- **DB tables/migrations:** n/a
- **Existing tests:** `test_every_core_module_imports_clean`, `test_makefile_targets_exist` (both pass)
- **Missing tests:** everything feature-level; `tests/isolation` in particular is a hard CI gate later (MVP-097) — the CI workflow's `isolation` job already emits a warning if it stays empty past that point
- **Known risks:** none currently; empty dirs are correctly deferred, filling in as owning tickets land
- **MVP relevance:** `tests/isolation` is safety-critical (cross-tenant leakage); rest scale with feature build-out
- **Recommended next action:** add tests alongside each feature ticket as it's built; no standalone action needed now

## Verification commands

```bash
# Python deps (uv manages Python 3.12 itself)
uv sync

# Lint
uv run ruff check .

# Type-check
uv run mypy core
uv run mypy migrations --exclude 'migrations/versions'

# Unit tests
uv run pytest -v

# Frontend type-check + build
cd web && npx tsc -b --noEmit && npm run build && cd ..

# Makefile targets resolve (dry run, no execution)
for t in dev migrate test seed; do make -n $t; done

# Docker stack — requires a reachable Docker daemon
make dev

# Migrations — requires a live Postgres (via `make dev` or a local instance)
uv run alembic upgrade head

# Alembic migration history (no DB needed)
uv run alembic history
```

## Current blockers

1. Docker daemon unreachable from this environment — `make dev` and a real `alembic upgrade head` against a container have never been executed end-to-end; only dry-run/static checks are verified.
2. Env var prefix mismatch in `infra/docker/docker-compose.dev.yml` (`DATABASE_URL`/`REDIS_URL`) vs. `core/common/config.py`'s `Settings` (`GROWTH_OPERATOR_` prefix) — latent, will misconfigure DB/Redis connections inside containers.
3. Zero database migrations exist — every module needing persistence is blocked on migration 001.
4. `langgraph` and `jsonschema` are named in the authoritative stack docs (for `core/runtime` and `core/catalog`/pack validation respectively) but are not yet in `pyproject.toml`.
5. Meta WABA verification (external, non-code, longest lead-time item per the vault's week-1 plan) — status unknown to this repo since it isn't a code artifact.
6. React 18 vs. 19 deviation in `web/` — spec says 18, scaffold installed 19; unresolved decision.
7. No git commits exist yet — all work is untracked.

## Recommended implementation order

1. Fix the docker-compose env var prefix bug (small, unblocks everything else that needs a live DB)
2. MVP-011 — OTP auth endpoints + migration 001 (`users, sessions, otp_challenges`)
3. MVP-012..013 — sessions/JWT, logout/revocation
4. MVP-014..016 — organizations/`/me`, RBAC roles + decorator, tenant `SET LOCAL` middleware + migrations 002–003
5. MVP-019, MVP-024 — messaging migration, audit chain writer (hard invariant, must land before any send path)
6. MVP-025..030 — outbox/consumer/dedupe/DLQ/typed event catalog
7. MVP-031..037 — WhatsApp ingress/send/suppression (contingent on WABA verification being complete)
8. MVP-038..044 — pack contracts/verifier/installer (first code that actually reads `verticals/jewelry`)
9. Continue per `docs/tickets/README.md` dependency order (catalog → pricing → runtime → approvals → workflows → ingestion → insights → frontend pages)

## First recommended ticket

**MVP-011 — OTP auth endpoints** (P0, dependency MVP-004 already satisfied). This is the first ticket that produces both a real database migration and a real API surface; every placeholder module downstream is gated behind it per the dependency graph in `docs/tickets/README.md`.

## Questions requiring founder decisions

1. WABA number: Srila's existing WhatsApp number vs. a new number for Priya (porting freezes the number for days) — must be decided before Meta submission.
2. IBJA gold-rate source: scrape vs. paid API vs. manual-first (manual is the documented hedge for MVP-051).
3. Owner approval channel: WhatsApp interactive only, or add PWA push (adds scope) — plan currently says WhatsApp-only for MVP.
4. Razorpay account entity: personal vs. new company (ties to a founder-IP legal question) — needed before payment links (MVP-053/054 area).
5. Data residency: Hetzner EU vs. India VPS for DPDP posture — not blocking for pilot scale, blocking before scale-up.
6. React 18 (per spec) vs. React 19 (as scaffolded) — pin down or update the doc.
7. Judge model choice for eval harness (cost vs. calibration) — needed by MVP-095, "start mid-tier, calibrate" is the current default.
