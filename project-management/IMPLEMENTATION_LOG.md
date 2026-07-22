# Growth Operator — Implementation Log

Append-only. One dated entry per ticket. Do not edit or delete prior entries — if a ticket is revisited, add a new entry referencing the old one.

---

## 2026-07-10 — MVP-001 · Monorepo scaffold

**Ticket:** [MVP-001](../docs/tickets/MVP-001.md) (bundles MVP-002..005 per [prompt 01-scaffold](../docs/implementation/prompts/claude-code/01-scaffold.md): docker-compose, CI pipeline, migration framework + RLS helper, config loader + error taxonomy)

**Approved plan:** Follow `docs/implementation/prompts/claude-code/01-scaffold.md` exactly — create the monorepo layout, pyproject, docker-compose dev stack, RFC7807 error taxonomy, layered config, Alembic async init + RLS helper, GitHub Actions CI, Makefile. No business logic. No auth.

**Files changed:**
- `pyproject.toml`, `uv.lock`, `.python-version`
- `core/__init__.py` and one `__init__.py` per submodule: `api, approvals, audit, catalog, channels, channels/whatsapp, common, events, ingestion, insights, mediation, packs, pricing, prompts, runtime, tenancy, workflows`
- `core/api/main.py`, `core/common/errors.py`, `core/common/config.py`, `core/worker.py`, `core/scheduler.py`
- `migrations/env.py`, `migrations/script.py.mako`, `migrations/README`, `migrations/lib/__init__.py`, `migrations/lib/rls.py`
- `alembic.ini`
- `infra/docker/docker-compose.dev.yml`, `infra/docker/Dockerfile.dev`, `infra/docker/Caddyfile.dev`
- `.github/workflows/ci.yml`
- `Makefile`, `scripts/dev_seed.py`
- `tests/unit/test_scaffold.py`
- `.gitignore`, `.dockerignore`
- `docs` (symlink -> `../Growth-Operator-Vault`)
- `verticals/jewelry/**` (27 files), `verticals/kirana/**` (16 files) — copied from `docs/verticals/`
- `web/` — Vite + React 18/19 + TS + Tailwind v4 + TanStack Query scaffold

**Migrations:** None. `migrations/versions/` created but empty — framework only.

**APIs:** None. `core/api/main.py` exposes a bare `FastAPI()` app with the RFC7807 exception handler registered; zero routes.

**Commands run:**
```
uv init / uv sync
uv run alembic init -t async migrations/alembic_env_tmp
uv run ruff check . / uv run ruff check --fix migrations/env.py
uv run mypy core / uv run mypy migrations --exclude 'migrations/versions'
uv run pytest -v
npm create vite@latest . -- --template react-ts
npm install / npm install -D tailwindcss @tailwindcss/vite / npm install @tanstack/react-query @tanstack/react-router
npx tsc -b --noEmit / npm run build
make -n dev / migrate / test / seed
```

**Tests:** `tests/unit/test_scaffold.py::test_every_core_module_imports_clean` (PASS), `::test_makefile_targets_exist` (PASS). No feature-level tests exist yet.

**Results:** ruff clean, mypy clean (23 core source files, 3 migrations files), pytest 2/2 passed, frontend `tsc`/`vite build` clean. `make -n` resolves all four targets.

**Known issues:**
- `infra/docker/docker-compose.dev.yml` sets `DATABASE_URL`/`REDIS_URL` but `core/common/config.py` expects `GROWTH_OPERATOR_`-prefixed vars — latent misconfiguration, not yet triggered (no code opens a DB/Redis connection yet). See [BLOCKERS.md](BLOCKERS.md).
- `make dev` / `alembic upgrade head` against a live container never verified end-to-end — sandboxed session had no reachable Docker daemon.
- `langgraph` (core/runtime) and `jsonschema` (core/catalog, pack validation) named in authoritative docs but not yet added to `pyproject.toml`.
- React 19.2.7 installed in `web/`; spec (`06-backend-plan.md`) names React 18.

**Commit hash:** `cf7536e` — "Initial monorepo scaffold (MVP-001..005) + implementation audit"

**Next action:** MVP-011 — OTP auth endpoints (see [CURRENT_TASK.md](CURRENT_TASK.md)).

---

## 2026-07-22 — MVP-011 · OTP auth endpoints (+ blocker #1 fix)

**Ticket:** [MVP-011](../docs/tickets/MVP-011.md) — phone-OTP sign-in. Also fixed BLOCKERS #1 (docker-compose env-var prefix) on the same branch, per the audit's guidance to fix it before the first live-DB ticket.

**Branch:** `feature/mvp-011-otp-auth` (not yet committed).

**Approved plan (founder decisions this session):**
- Fix blocker #1, then implement full MVP-011.
- WABA verification uses Srila's existing number (recorded in DECISIONS.md).
- `users` is a **global** table — no `tenant_id`, no RLS on identity tables; org membership deferred to `user_orgs` (migration 002). Resolves a v1-schema-vs-migration-order conflict (recorded in DECISIONS.md).
- Add `argon2-cffi` for OTP/token hashing (recorded in DECISIONS.md).

**Files changed:**
- `infra/docker/docker-compose.dev.yml` — `DATABASE_URL`/`REDIS_URL` → `GROWTH_OPERATOR_`-prefixed on api/worker/scheduler (blocker #1).
- `pyproject.toml` — add `argon2-cffi>=23.1`; ruff bugbear `extend-immutable-calls` for FastAPI `Depends`; mypy override ignoring `jose.*` missing stubs.
- `core/common/config.py` — add `otp_dev_echo` flag (default False).
- `core/api/main.py` — include auth router; startup guard `assert_otp_config_safe`.
- `core/common/db.py` (new) — async engine + `get_session` dependency.
- `core/tenancy/auth.py` (new) — pure OTP logic: E.164 validation, code gen, argon2 hashing, `Challenge` state machine, JWT mint/decode.
- `core/tenancy/otp_delivery.py` (new) — dev-only stderr OTP echo behind flag + prod fail-closed guard; no-op adapter otherwise.
- `core/tenancy/repository.py` (new) — async-SQL data access for users/sessions/otp_challenges.
- `core/tenancy/router.py` (new) — `POST /v1/auth/otp`, `POST /v1/auth/otp/verify`.
- `migrations/versions/ccd4ed78aeef_001_identity.py` (new) — migration 001.
- `tests/unit/test_auth_otp.py` (new) — 28 assertions across the pure logic.

**Migrations:** 001 `identity` (rev `ccd4ed78aeef`, down_revision base) — `users`, `sessions`, `otp_challenges`. Global tables, **no RLS by design** (DECISIONS.md 2026-07-22). `CREATE EXTENSION citext` for case-insensitive email. Offline SQL generation verified (`alembic upgrade head --sql`); live `upgrade head` NOT run — no Docker daemon reachable this session (BLOCKERS #2).

**APIs:** `POST /v1/auth/otp` (202, `{status:"sent"}`), `POST /v1/auth/otp/verify` (200, `{access_token, refresh_token, token_type}`). Auth failures use plain HTTP (422 bad phone, 401 invalid/expired/mismatch, 429 throttled/locked) — NOT canonical error codes (§13, closed taxonomy).

**Events / frontend:** none.

**Commands run:** `uv sync`; `uv run alembic revision -m "001_identity"`; `uv run ruff check .` (PASS); `uv run mypy core` (PASS, 28 files); `uv run mypy migrations --exclude 'migrations/versions'` (PASS); `uv run pytest -q` (28 passed); `uv run alembic upgrade head --sql` (valid SQL); TestClient smoke on both routes (422 on bad phone). Live-DB path (`alembic upgrade head`, real verify happy-path, staging phone smoke) NOT run — BLOCKED on Docker/staging.

**Known issues / deferred:**
- Live-DB verification blocked (no Docker) — router happy-path, migration upgrade/downgrade against Postgres, cross-restart session behavior unverified. See BLOCKERS #2.
- Acceptance criterion "OTP on founder's real phone in staging" is BLOCKED (needs staging + WABA/SMS provider; provider integration is explicitly out of MVP-011 scope).
- Resend throttle implemented via the durable `otp_challenges.last_sent_at` column, not Redis (spec suggested Redis). Correct + testable; revisit if a distributed throttle is needed.
- starlette deprecation warning: `HTTP_422_UNPROCESSABLE_ENTITY` constant renamed upstream — cosmetic.

**Commit hash:** none yet — awaiting founder review.

**Next action:** Founder reviews diff; run `make dev` + `uv run alembic upgrade head` + `pytest tests/isolation` locally to clear BLOCKERS #2; then select MVP-012 (sessions + JWT issue/refresh).

---

## 2026-07-22 — MVP-011 amendment · Interim email OTP channel

**References:** amends the MVP-011 entry above (same branch `feature/mvp-011-otp-auth`, uncommitted). Founder-directed, to avoid the Meta WABA lead-time. See DECISIONS.md 2026-07-22 and [TODO.md](TODO.md).

**Change:** OTP channel is now selectable via `GROWTH_OPERATOR_OTP_CHANNEL` (default **email**); email replaces phone as the interim login identifier, phone path retained behind the flag.

**Files changed (delta):**
- `migrations/versions/…001_identity.py` — `users.phone` now nullable + `CHECK (phone OR email present)`; `otp_challenges` generalized `phone` → `channel` + `identifier` (+ CHECK channel in ('email','phone')); index → `(channel, identifier, created_at DESC)`. (Edited in place — unapplied/uncommitted.)
- `core/common/config.py` — add `otp_channel: Literal["email","phone"] = "email"`.
- `core/tenancy/auth.py` — add `OtpChannel`, `validate_email`, `validate_identifier`; `Challenge`/`new_challenge` use `(channel, identifier)`.
- `core/tenancy/repository.py` — challenges keyed by `(channel, identifier)`; `get_or_create_user(channel, identifier)` writes the matching column (column name from enum, never request input).
- `core/tenancy/router.py` — request body `{identifier}`; channel from settings; channel-aware 422 messages.
- `core/tenancy/otp_delivery.py` — `send(channel, identifier, code)`; real `EmailOtpDelivery` still to be written (gated, §10.4).
- `tests/unit/test_auth_otp.py` — email + dispatch tests added; phone validation retained. **37 passed.**

**Commands:** `uv run ruff check .` (PASS) · `uv run mypy core` (PASS) · `uv run pytest -q` (**37 passed**) · `alembic upgrade head --sql` (valid: channel/identifier + CHECKs) · TestClient (email channel default; phone→422, bad email→422; valid email proceeds to DB then BLOCKED, no Postgres).

**Known issues / deferred:** real email provider + credentials + approval needed before staging sends (only dev echo active) — TODO #2. Live-DB verify path still BLOCKED (#2). Restore phone OTP + real Meta when ready — TODO #1/#2.

**Next action:** unchanged — founder reviews; clear BLOCKERS #2 locally; then MVP-012.

---

## 2026-07-22 — MVP-011 follow-on · EmailOtpDelivery adapter + login-page demo + status refresh

**References:** continues the MVP-011 work (same branch, uncommitted). Founder-directed: "draft EmailOtpDelivery now", "update status.html", "ready to view the demo". Not a new ticket selection.

**Backend — `EmailOtpDelivery`:**
- `core/common/config.py` — add `otp_email_enabled` (default False) + `smtp_host/port/username/password/from`.
- `core/tenancy/otp_delivery.py` — `EmailOtpDelivery` (stdlib `smtplib` STARTTLS, no new dep); `get_otp_delivery` precedence dev-echo > email (when enabled+configured) > noop; `assert_otp_config_safe` now also fails closed if email enabled without full SMTP config (§10.4).
- `core/tenancy/router.py` — OTP send offloaded via `run_in_threadpool` (blocking SMTP must not stall the loop).
- `tests/unit/test_otp_delivery.py` (new) — 8 selection/guard tests. **Real sends stay OFF by default** (external side effect gated per §10.4).

**Frontend — login-page demo (MVP-082 preview slice, unblocked by MVP-011):**
- `web/src/api.ts`, `web/src/App.tsx`, `web/src/vite-env.d.ts` — 2-step email-OTP login (request code → verify → token) against the real API contract, with a **Simulate** toggle so it runs with no backend. `tsc -b` + `vite build` + `oxlint` clean.

**Status dashboard:**
- `project-management/status.json` + `status.html` — meta refreshed (generated 2026-07-22, corrected the stale "zero migrations / zero routes" grounding note); added **Track E "Access & Identity"** (E1–E5) + an Identity meter card in the render JS. Both files valid JSON and byte-for-byte in sync; self-contained (offline-viewable).

**Commands:** `uv run ruff check .` (PASS) · `uv run mypy core` (PASS) · `uv run pytest -q` (**45 passed**) · `web: npm run build` (PASS) · `npm run lint` (PASS) · JSON validation of both status files (PASS, ids unique).

**Known issues / deferred:** real email provider + creds + approval still needed to actually send (TODO #2); live-DB path still BLOCKED (#2); login page is a preview slice, not full MVP-082.

**Next action:** unchanged — founder reviews; then select the next ticket (MVP-012 recommended).

---

## 2026-07-22 — MVP-011 live-DB verification (BLOCKERS #2 resolved)

**Context:** Founder installed OrbStack + Docker Desktop. Verified MVP-011 end-to-end against a real database — closes the long-standing "no live DB" gap.

**What ran:**
- `docker compose -f infra/docker/docker-compose.dev.yml up -d postgres redis` → both healthy (pgvector/pgvector:pg16 on 5432, redis:7 on 6379).
- `uv run alembic upgrade head` → migration 001 applied; `downgrade base` drops all tables; `upgrade head` recreates — down-migration proven. Columns/indexes/CHECKs inspected via psql.
- `tests/integration/test_auth_flow.py` (new) — fully async (httpx ASGI transport; disposes the engine between tests to avoid cross-loop asyncpg pool reuse). Skips cleanly when no migrated DB is reachable. Covers: request→verify issues a token pair and writes real `users`/`sessions` rows (token `sub` == created user id, exactly one session); wrong code → 401 with no user created; lockout after 5 attempts → 429 even with the correct code.

**Result:** `uv run pytest` → **48 passed** (45 unit + 3 integration). ruff + mypy clean.

**Files changed (delta):** `tests/integration/test_auth_flow.py` (new). Tracking: BLOCKERS #2 → RESOLVED; CURRENT_TASK acceptance criteria updated; status.* E2/E3 → DONE + grounding note refreshed.

**Known issues / deferred:** app containers (api/worker/scheduler) not yet booted via full `make dev` (only data services); real email inbox delivery still needs a provider (TODO #2) + staging.

**Next action:** founder reviews; then MVP-012 (sessions + JWT refresh/rotation).
