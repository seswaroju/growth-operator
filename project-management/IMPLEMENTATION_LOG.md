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

---

## 2026-07-22 — MVP-006 – MVP-010 · platform foundations (the leapfrogged batch)

**Context:** Founder directed implementing the tickets skipped after the scaffold, before continuing. Branch `feature/mvp-006-010-platform-foundations` off main (which now includes merged MVP-011, `eeab4e2`). Not yet committed — awaiting founder review.

**MVP-007 · Health + readiness — DONE**
- `core/api/health.py`: `/healthz` (liveness, no dep probes) + `/readyz` (pg + redis + alembic-head, 1s timeouts, 503 when not ready). Wired into app; compose api healthcheck → `/healthz`.
- Tests: `tests/unit/test_health.py` (liveness), `tests/integration/test_health.py` (readyz 200 healthy; 503 when head mismatched). Verified live.

**MVP-010 · Lint guards — DONE (P0)**
- `scripts/guards.py`: core↛verticals, industry-nouns (core/ + web/src), float-money (`float(` near `*_minor`), send-call-sites (`messages.send` only under `core/channels/`). Allowlist with mandatory justification (`scripts/lint-allowlist.txt`). `scripts/lint.sh` wrapper; `.importlinter` contract; CI step added.
- Tests: `tests/unit/test_lint_guards.py` — each guard red on its violation; repo clean; allowlist rules. Dropped generic "ring" from the denylist (false-positives on Tailwind `focus:ring`).

**MVP-006 · OTel + structured logging — PARTIAL**
- `core/common/telemetry.py`: env-gated tracer/OTLP + FastAPI/asyncpg/redis/httpx instrumentation; `ScrubbingJsonFormatter` masking E.164 + OTP codes (§10.2/§10.3); `org_id`/`trace_id` injection. No-op unless `OTEL_EXPORTER_OTLP_ENDPOINT` set (off in tests). Deps: `opentelemetry-instrumentation-{asyncpg,redis,httpx}`; mypy override for `opentelemetry.*`.
- Tests: `tests/unit/test_telemetry.py` (scrubber + formatter). Full "one trace webhook→consumer→send" acceptance is blocked on those components (MVP-032+).

**MVP-008 · Secrets via SOPS — PARTIAL (scaffold)**
- `.sops.yaml` (placeholder recipient), `.pre-commit-config.yaml` (gitleaks + ruff + guards), `secrets/README.md` + `secrets/dev.example.yaml` (fake), `scripts/decrypt-secrets.sh` (fail-loud entrypoint). `config.py`: `require_secrets_file` + `assert_secrets_available` (boot fail-closed, wired into main). `.gitignore` protects plaintext/keys; `pre-commit` dev dep.
- Tests: `tests/unit/test_secrets.py`. Real age key + `*.enc.yaml` + running gitleaks are the founder's step (§10.1).

**MVP-009 · Staging environment — BLOCKED (scaffold only)**
- `infra/terraform/staging/*` (Hetzner CPX21 + firewall + ssh key; DNS/Meta stubbed) and `.github/workflows/deploy-staging.yml` (deploy-on-merge, migrations before swap, `/readyz` smoke; gated behind a `staging` env + `STAGING_ENABLED`). **Un-applied.** Needs Hetzner account, domain, data-residency decision (BLOCKERS #8), Meta access.

**Also:** `pyproject.toml` pytest `--import-mode=importlib` (fixes unit/integration same-name collisions).

**Commands:** `uv run ruff check .` (PASS) · `uv run mypy core` (PASS, 30 files) · `uv run python scripts/guards.py` (PASS) · `uv run pytest -q` (**69 passed**) · YAML workflows parse. terraform not installed (scaffold not fmt-validated).

**Next action:** founder reviews + approves commit; then MVP-012. Founder to unblock MVP-009 (account/domain/residency) and MVP-008 (age key) when ready.

## 2026-07-29 — MVP-012 · Sessions + JWT issue/refresh

**Ticket:** [MVP-012](../docs/tickets/MVP-012.md) · P0 · deps MVP-011. Branch `feature/mvp-012-sessions-jwt` off merged main. First ticket of the founder-directed 012–030 batch (implement → verify live → log; verified against live postgres+redis this session).

**Approved plan:** Rotating refresh on the existing migration-001 `sessions` table (no new migration). The `sessions` row (`sid`) is the token family; `token_hash` holds only the current valid refresh token. Reuse of a rotated token on a live session revokes the family; concurrent rotation resolves to one winner via an atomic conditional UPDATE. Audit-on-reuse is interim (structured log) until `audit_log` (migration 006 / MVP-024).

**Files changed:**
- `core/tenancy/tokens.py` (new) — `refresh_session()` orchestrator; `RefreshOutcome` {OK, INVALID, REUSE, RACE_LOST}; reuse detection + interim security log.
- `core/tenancy/repository.py` — `SessionRow`, `get_session_row`, `rotate_session_token` (atomic `UPDATE … WHERE token_hash=:expected AND revoked_at IS NULL RETURNING`, slides `expires_at`), `revoke_session` (idempotent; reused by MVP-013 logout).
- `core/tenancy/router.py` — `POST /v1/auth/refresh`: 200 pair · 409 RACE_LOST · uniform 401 for INVALID/REUSE (no oracle; family already revoked server-side on REUSE).
- `core/tenancy/auth.py` — added random `jti` nonce to `issue_refresh_token` (fixes a real bug: same-second rotation minted a byte-identical token, which would blind reuse detection in that window).
- `tests/unit/test_tokens.py` (new) — DB-free INVALID branches (malformed, wrong type, missing sid, bad signature).
- `tests/integration/test_refresh_flow.py` (new, live DB) — rotate + old-token-rejected; reuse revokes family (new token also dead; `revoked_at` set); rotation race one-winner + family survives; orchestrator returns RACE_LOST not REUSE when a valid token loses the update.

**Migration:** none. **DB tables:** none (uses `sessions` from 001). **Deps:** none. **Frontend:** not done — no owner-facing auth client is wired yet (ticket's "silent refresh in api client" deferred with the auth UI; noted below).

**API:** `POST /v1/auth/refresh` — req `{refresh_token}` → `TokenPair {access_token, refresh_token, token_type}`; 401 invalid/reuse, 409 concurrent-refresh.

**Commands:** `uv run ruff check .` (PASS) · `uv run mypy core` (PASS, 31 files) · `uv run pytest -q` (**77 passed**, 0 skipped — live postgres+redis up) · new tests `-v` (8 passed).

**Requirement → evidence:**
| Criterion | Impl | Test | Result |
|---|---|---|---|
| Stolen old refresh rejected after rotation | tokens.py, repository.py | `test_refresh_rotates_and_old_token_rejected` | PASS (live) |
| Reuse detection revokes session family + audit entry | tokens.py | `test_reuse_of_rotated_token_revokes_family` | PASS (live); **audit entry = interim structured log** until MVP-024 |
| Rotation race → one wins, family survives | repository.rotate_session_token | `test_rotation_race_one_wins_family_survives`, `test_refresh_session_returns_race_lost_when_hash_moved` | PASS (live) |
| Reuse of rotated refresh revokes family | tokens.py | `test_reuse_of_rotated_token_revokes_family` | PASS (live) |

**Known issues / deferred:**
- Audit entry on reuse is a structured log, not the immutable hash-chain entry — real linkage is an add-back at MVP-024 (TODO.md #7).
- Frontend silent-refresh not implemented (no auth client scaffold yet); revisit with the owner login UI.
- Refresh lifetime slides (each rotation extends `expires_at` to now+30d) — documented semantics.

**Next action:** MVP-013 (logout + logout-all) — reuses `revoke_session`; ship together with 012 per the ticket rollout note.

## 2026-07-29 — MVP-013 · Logout + revocation

**Ticket:** [MVP-013](../docs/tickets/MVP-013.md) · P1 · deps MVP-012. Branch `feature/mvp-012-sessions-jwt` (ships with 012 per the ticket rollout note).

**Approved plan:** `POST /v1/auth/logout` (this session) + `POST /v1/auth/logout-all` (all sessions for the user), on the migration-001 `sessions` table (`revoked_at` already present). Revocation bites at the next refresh; the stateless access token keeps working until expiry (documented semantics).

**Files changed:**
- `core/tenancy/tokens.py` — `read_session_ref()`: verifies signature, ignores expiry (you may log out an expired session), returns `(user_id, session_id)` or None.
- `core/tenancy/repository.py` — `revoke_all_user_sessions()` (returns count of revoked).
- `core/tenancy/router.py` — `POST /v1/auth/logout` (204, idempotent best-effort no-op on bad token) + `POST /v1/auth/logout-all` (204); `LogoutRequest`.
- `tests/integration/test_logout_flow.py` (new, live DB) — logout→can't-refresh + idempotent re-logout; logout-all kills a second device (2 live sessions → 0); garbage token → 204 no-op.

**Migration:** none. **DB tables:** none. **Deps:** none. **Frontend:** not done — sign-out UI (AppShell + settings) deferred with the auth client (no auth UI wired yet).

**API:** `POST /v1/auth/logout`, `POST /v1/auth/logout-all` — req `{refresh_token}` → 204.

**Commands:** `uv run ruff check .` (PASS) · `uv run mypy core` (PASS, 31 files) · `uv run pytest -q` (**80 passed**, 0 skipped).

**Requirement → evidence:**
| Criterion | Impl | Test | Result |
|---|---|---|---|
| Revoked session cannot refresh | logout + tokens.refresh_session | `test_logout_revokes_current_session_cannot_refresh` | PASS (live) |
| logout-all revokes every session for the user | repository.revoke_all_user_sessions | `test_logout_all_kills_second_device` | PASS (live) |
| Revoked session's access token lives until expiry then dies | documented semantics (stateless access) | asserted at refresh boundary | PASS (documented) |

**Next action:** MVP-014 (organizations + /me, migration 002 — first org-scoped RLS migration).

## 2026-07-29 — MVP-014 · Organizations + /me

**Ticket:** [MVP-014](../docs/tickets/MVP-014.md) · P0 · deps MVP-012. Branch `feature/mvp-012-sessions-jwt`. First org-scoped (RLS) migration.

**Approved plan / decisions:** migration 002 (`organizations` = tenant root, no RLS; `user_orgs` = membership, RLS). Founder approved (DECISIONS.md 2026-07-29) the **`app.user_id` self-policy** on `user_orgs` so `/me`, org-create idempotency, and refresh can read a user's membership before any org context exists; requests set two GUCs (`app.user_id` always, `app.org_id` when known). Refresh + OTP-verify now **re-derive org_id + role from user_orgs** and embed them in the access token (closes a real gap: a bare refresh token carries no org_id, so every 15-min refresh previously dropped tenant context).

**Files changed:**
- `migrations/versions/f9b698afc8b8_002_orgs.py` (new) — `organizations`, `user_orgs` (PK (user_id,org_id), role CHECK owner|staff|founder), `apply_rls('user_orgs')` + `p_self` SELECT self-policy.
- `migrations/lib/rls.py` — **hardened** `apply_rls`: `NULLIF(current_setting('app.org_id', true), '')::uuid` so a pooled connection's empty-string GUC fails closed (0 rows) instead of raising `''::uuid` (500). Deviates from the literal SQL in multi-tenant-rls.md but preserves its "no context = no rows" intent. **Pending founder ratification.**
- `core/tenancy/repository.py` — `set_user_context`, `set_org_context`, `primary_membership`, `get_organization`, `insert_organization` (vertical omitted when None → DB default, keeps Rule Zero), `insert_user_org`, `get_user`.
- `core/tenancy/deps.py` (new) — `get_current_auth` Bearer-access dependency (precursor to MVP-016 middleware).
- `core/tenancy/orgs_router.py` (new) — `POST /v1/orgs`, `GET /v1/me`.
- `core/tenancy/tokens.py`, `core/tenancy/router.py` — refresh + verify embed org_id/roles.
- `core/api/main.py` — mount orgs_router.
- `tests/integration/test_orgs_flow.py` (new, live DB) — create→owner + JWT reissue with org_id; idempotent per user; /me before/after; refresh re-embeds org_id; /me needs bearer; **RLS isolation under a constrained non-bypass role** (org-scoped, fail-closed, self-policy).

**Migration 002:** upgrade + downgrade + re-upgrade verified live; RLS forced on user_orgs; policies p_self(SELECT)/p_tenant(ALL)/p_tenant_ins(INSERT) confirmed via catalog.

**API:** `POST /v1/orgs` (Bearer; Idempotency-Key header accepted) → `{org, access_token}`; `GET /v1/me` → `{user, org|null, roles}`. 401 without bearer.

**Commands:** guards PASS · ruff PASS · mypy core PASS (33) · mypy migrations PASS · `pytest -q` **86 passed**, 0 skipped.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| org create idempotent via Idempotency-Key (same key → same org id) | `test_org_create_is_idempotent_per_user` | PASS (live) |
| JWT reissued with org_id claim (before/after) | `test_create_org_grants_owner_and_reissues_jwt_with_org_id` | PASS (live) |
| refresh preserves org_id (gap closed) | `test_refresh_reembeds_org_id` | PASS (live) |
| user_orgs RLS isolates (org-scope, fail-closed, self) | `test_user_orgs_rls_isolates_under_constrained_role` | PASS (live, constrained role) |

**Known issues / BLOCKER:**
- **RLS not enforced for the app yet** — the app connects as `growth_operator` (superuser, bypassrls). Policies are defined + proven under a constrained role, but real enforcement needs a non-bypass `app_rw` role + pointing the app at it. → **MVP-016** (BLOCKERS #11).
- `apply_rls` NULLIF hardening awaits founder ratification (see above).
- Frontend onboarding org step not built (no web auth client yet).

**Next action:** founder ratifies the apply_rls hardening; MVP-015 (RBAC roles + @requires, migration 003).

## 2026-07-29 — MVP-015 · RBAC roles + @requires decorator

**Ticket:** [MVP-015](../docs/tickets/MVP-015.md) · P0 · deps MVP-014. Branch `feature/mvp-012-sessions-jwt`.

**Approved plan:** Three fixed roles (owner/staff/founder) with constant-based enforcement. `@requires(perm)` dependency resolves perms from `ROLE_PERMISSIONS` (no per-request DB I/O); migration 003 seeds the roles/permissions/role_permissions catalog FROM those constants (drift-tested). 403 is RFC7807 `application/problem+json` naming the missing permission.

**Files changed:**
- `core/tenancy/permissions.py` (new) — role + permission constants, `ROLE_PERMISSIONS`, `permissions_for`/`has_permission`. Single source of truth (append-only in MVP).
- `core/tenancy/rbac.py` (new) — `requires(perm)` dependency factory; `PermissionDenied` → `permission_denied_handler` (403 problem+json with `permission` field + detail); `register_rbac_handlers`. Not a canonical `GrowthOperatorError` code (§13) — a route-authz problem, like the auth 401s.
- `core/api/main.py` — register RBAC handler.
- `migrations/versions/0cf4c4b7b1d3_003_rbac.py` (new) — `roles`, `permissions`, `role_permissions` (global catalog, no RLS) + `user_roles` (org-scoped, `apply_rls`); idempotent seed of 3 roles / 8 perms / grants.
- `pyproject.toml` — add `core.tenancy.rbac.requires` to ruff B008 immutable-calls (same DI idiom as `fastapi.Depends`).
- `tests/unit/test_rbac.py` (new) — role×perm matrix (incl. staff-cannot-resolve-approvals), no-role denies all, and HTTP: owner 200 / staff 403 problem+json naming the perm / missing token 401.
- `tests/integration/test_rbac_seed.py` (new, live DB) — seed ↔ constants drift test; catalog completeness.

**Migration 003:** upgrade + re-upgrade (seed idempotent) + downgrade + re-upgrade verified live; roles=3, perms=8, grants owner7/staff2/founder8; `user_roles` RLS forced.

**Commands:** guards PASS · ruff PASS · mypy core PASS (35) · `pytest -q` **101 passed**, 0 skipped.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| staff cannot resolve approvals | `test_rbac::test_permission_matrix[staff-approvals:resolve]`, `test_staff_denied_with_problem_json_naming_permission` | PASS |
| 403 problem names the missing permission | `test_staff_denied_with_problem_json_naming_permission` | PASS (problem+json, `permission` field + detail) |
| matrix roles×5 sample perms | `test_permission_matrix` (8 cases) | PASS |
| migration 003 seeds match constants | `test_rbac_seed::test_seed_matches_constants` | PASS (live) |

**Deferred (disclosed):**
- Test case "founder cross-org route without explicit audit wrapper fails a lint test" — **not implemented**: no founder cross-org routes exist yet and audit lands in MVP-024. Revisit when both exist (add a guard once there is something to lint).
- `user_roles` table created per the migration plan but not wired into enforcement — MVP uses `user_orgs.role` (JWT `roles` claim) as the assignment source; `user_roles` is the normalized table for later.

**Next action:** MVP-016 · tenant middleware `SET LOCAL app.org_id` + `get_db` dependency + worker job wrapper — and the `app_rw` non-BYPASSRLS role that makes RLS actually enforced (BLOCKERS #11).

## 2026-07-29 — MVP-016 · Tenant middleware (SET LOCAL) + app_rw role — resolves BLOCKERS #11

**Ticket:** [MVP-016](../docs/tickets/MVP-016.md) · P0 · deps MVP-014. Branch `feature/mvp-016-tenant-middleware`. Makes RLS **actually enforced** for the app (was defined-but-inert; BLOCKERS #11).

**Approved plan:** Founder directed "unblock #11 then test." Two-URL privilege split + tenant context dependency + worker wrapper + session-SET ban lint.

**Files changed:**
- `infra/db/roles.sql` (new) — idempotent `app_rw`: NON-superuser, **NOBYPASSRLS**, `statement_timeout=5s`, DML grants + `ALTER DEFAULT PRIVILEGES` so future migrations' tables are auto-granted. Dev password is a throwaway local credential; staging/prod source it from SOPS.
- `core/common/config.py` — `database_url` default → `app_rw` (runtime); new `database_migrator_url` → owner (DDL). The app enforces RLS; alembic keeps DDL rights.
- `migrations/env.py` — alembic now uses `database_migrator_url`.
- `core/tenancy/middleware.py` (new) — `get_db` (per-request txn with `SET LOCAL app.org_id`/`app.user_id` from the verified access token; no token → no context → RLS zero rows) and `org_scoped_session` (worker/job wrapper, one org per txn). Uses `set_config(..., true)` (txn-local) — decodes the token itself to avoid the BaseHTTPMiddleware/contextvar propagation pitfall. Stamps `telemetry.org_id_var` for log correlation (MVP-006).
- `core/tenancy/orgs_router.py` — `GET /v1/me` converted to `get_db` (the required "one endpoint on the dependency" demo).
- `scripts/guards.py` — 5th guard `session-set-ban`: no session-level `SET app.*` / `set_config('app.*', v, false)` (txn-local only).
- `infra/docker/docker-compose.dev.yml` — api/worker/scheduler → `app_rw` DATABASE_URL + owner MIGRATOR_URL; postgres mounts `roles.sql` into initdb (fresh volume auto-creates app_rw).
- `Makefile` — `db-roles` (apply roles.sql) + `bootstrap` (db-roles + migrate).
- `tests/conftest.py` (new) — session-autouse fixture ensures `app_rw` exists (runs roles.sql via the migrator connection).
- `tests/integration/*` (6 files) — privileged `_dsn()` helpers switched to `database_migrator_url` (owner) since the app-under-test now runs as app_rw.
- `tests/isolation/test_tenant_context.py` (new) — end-to-end isolation through the REAL app under app_rw: request sees only its JWT org, no token → zero rows (fail closed), A can't see B, and the worker wrapper isolates.
- `tests/unit/test_lint_guards.py` — session-set-ban red-on-violation + allows txn-local.

**Migration:** none (no schema change). **DB roles:** app_rw created + verified `super=false bypassrls=false`.

**Commands:** guards PASS (5) · ruff PASS · mypy core PASS (36) · mypy migrations PASS · `pytest -q` **107 passed**, 0 skipped. **Live app smoke** (uvicorn as app_rw): `/healthz` 200, `/readyz` 200 (pg+redis+migration-head via app_rw grants), OTP→verify→`POST /orgs`→`GET /me`→refresh(re-embeds org_id)→`/me` no-token 401 — all correct.

**Requirement → evidence:**
| Criterion | Test / evidence | Result |
|---|---|---|
| probe query inside request returns JWT org | `test_tenant_context::test_request_sees_only_its_own_org` | PASS (live, app_rw) |
| unset context → zero rows | `test_tenant_context::test_no_token_sees_zero_rows` | PASS (live) |
| worker job carries org_id / scoped txn | `test_tenant_context::test_worker_org_scoped_session_isolates` | PASS (live) |
| session-SET banned by lint | `test_lint_guards::test_session_set_ban_goes_red_on_session_set` | PASS |
| demo: one endpoint on the dependency | `GET /v1/me` via `get_db` | done |

**Known / deferred:**
- **BLOCKERS #11 RESOLVED** (see below).
- Full isolation suite (MVP-097) and a dedicated PgBouncer txn-mode harness are later; the empty-string fail-closed (apply_rls NULLIF) + no-context test approximate the pooled-connection case now.
- The compose **app image** still hasn't been booted via `make dev` (smoke used host uvicorn as app_rw); staging deploy (MVP-009) must run `roles.sql` before the app starts.

**Next action:** commit MVP-016; then MVP-018 (API keys) or founder's pick.

## 2026-07-29 — MVP-018 · API keys (service auth)

**Ticket:** [MVP-018](../docs/tickets/MVP-018.md) · P2 · deps MVP-015. Branch `feature/mvp-016-tenant-middleware` (batched with 016/017/019).

**Files:** `migrations/versions/5b648aeb6773_004_api_keys.py` (api_keys +RLS + unique key_hash + `resolve_api_key` SECURITY DEFINER fn for the RLS-exempt auth lookup); `core/tenancy/api_keys.py` (SHA-256 keys — high-entropy so no argon2; `require_key_scope` dep sets org context from the key + enforces scope + records last_used; founder-only `POST /v1/api-keys`); `infra/db/roles.sql` (grant app_rw EXECUTE on functions + default privs); `core/api/main.py` (mount); `tests/integration/test_api_keys_flow.py`.

**Decisions:** keys hashed with SHA-256 (indexable exact-match; keys are high-entropy). Revocation is immediate (no cache) — trivially within the ticket's ≤60s bound; the cache is a deferred optimization. Auth is a separate service-key path (`require_key_scope`) alongside JWT — full single-dependency unification deferred (keys serve service endpoints; first consumer MVP-097).

**Requirement → evidence (all live):** key auth sets org context → `test_founder_issues_key_and_it_authenticates_with_org_context`; revoked key rejected → `test_revoked_key_is_rejected`; scope enforcement → `test_key_without_scope_is_forbidden`; founder-only → `test_non_founder_cannot_issue_key`; last_used updates → asserted. **5 tests pass.**

## 2026-07-29 — MVP-019 · Messaging migration 005

**Ticket:** [MVP-019](../docs/tickets/MVP-019.md) · P0 · deps MVP-016. Schema-only (DDL + RLS + indexes, no service code).

**Files:** `migrations/versions/306009477ea2_005_messaging.py`; `tests/isolation/test_messaging_rls.py`.

**Tables:** channels, contacts, conversations, messages, message_templates, suppressions (**6 org-scoped +RLS**) + **webhook_events (global, no RLS)**. Adaptations (DECISIONS 2026-07-30): `tenant_id`→`org_id`; `messages` gains denormalized `org_id`; `webhook_events` global (pre-tenant raw ingress can't be org-scoped) with `UNIQUE (provider, external_id)`; `messages.audit_id` / `conversations.assigned_agent` are plain uuids until their tables exist.

**Requirement → evidence (live, probed as app_rw):** per-table isolation (6 tables — org sees only its rows; no context → 0) → `test_each_table_isolated_under_app_rw`; unique (provider, external_id) clean conflict → `test_webhook_events_provider_external_id_unique`. Migration up/down/re-up verified. **2 tests pass.**

## 2026-07-29 — MVP-017 · Staff invite (seed only)

**Ticket:** [MVP-017](../docs/tickets/MVP-017.md) · P2 · deps MVP-015. Flag-gated.

**Files:** `migrations/versions/50583d342beb_invites.py` (invites — **global**, expiring, appended after 005 per DECISIONS 2026-07-30); `core/tenancy/invites.py` (SHA-256 token; owner-only `POST /v1/orgs/invites` + `POST /v1/orgs/invites/{token}/accept` joining as staff); `core/common/config.py` (`invites_enabled`, default false); `core/api/main.py` (mount); `tests/integration/test_invites_flow.py`.

**Decisions:** gated via `Settings.invites_enabled` (config kill-switch) until the real per-tenant `invites.enabled` flag lands with MVP-022; endpoints 404 when off. Accept joins the invited org with the **staff** role only, under that org's tenant context.

**Requirement → evidence (all live):** invite expires after 7 days → `test_expired_invite_is_rejected` (410); accepted invite grants staff only → `test_owner_invites_and_staff_accepts_as_staff_only`; flag-off hides it → `test_invites_disabled_returns_404`; staff can't invite → `test_staff_cannot_invite`. **4 tests pass.**

**Batch gates (016–019):** ruff · mypy core (38) · mypy migrations · guards (5) · **pytest 118 passed, 0 skipped**. Migrations linear: 001→002→003→004→005→invites.

## 2026-07-30 — MVP-024 · Audit chain writer (ADR-007)

**Ticket:** [MVP-024](../docs/tickets/MVP-024.md) · P0 · deps MVP-016. Branch `feature/mvp-024-audit-chain`. Implements docs/21-platform/audit-logging.md + prompt 03.

**Files:** `migrations/versions/e70f466c605e_006_audit.py` (audit_log +RLS, append-only REVOKE + BEFORE UPDATE/DELETE trigger, UNIQUE(org_id,seq); dedupe_consumer global); `core/audit/writer.py` (`write`, `verify_capability`, `write_outcome`, `verify_chain`, `canonical_json`); `core/audit/taxonomy.py` (action constants); `core/audit/__init__.py`; `scripts/audit-verify.py`; `infra/db/roles.sql` (keep audit_log UPDATE/DELETE revoked); `tests/unit/test_audit_chain.py`; `tests/integration/test_audit_writer.py`.

**Design:** per-org hash chain, `entry_hash = sha256(prev_hash + canonical_json(immutable fields))`; log-then-act in the caller's txn; `AuditId` is a 10-minute capability. **Per-org advisory transaction lock** (`pg_advisory_xact_lock` keyed by a 64-bit hash of org_id) serializes writes within an org and, unlike a FOR UPDATE head-lock, also covers the genesis write — different orgs never contend. canonical_json is a stdlib JCS-compatible form (no floats in audit payloads); no new dependency added.

**Requirement → evidence (all live):**
| Criterion | Test | Result |
|---|---|---|
| 1000-write / 3-org concurrency, no gaps, p95<3ms | `test_concurrent_per_org_no_gaps_and_latency` | PASS — **p95 measured 1.12ms**, seq contiguous per org |
| tampered row detected at exact seq | `test_tamper_detected_by_verify_chain_and_script` (+ `scripts/audit-verify.py`) | PASS (break at seq 3) |
| chain continuity | `test_write_builds_chain_and_verifies` + unit `verify_chain` suite | PASS |
| capability expiry | `test_capability_expiry_and_match` | PASS |
| trigger blocks UPDATE/DELETE | `test_append_only_trigger_blocks_update_and_delete` | PASS (owner blocked too) |

**Commands:** ruff · mypy core (40) · mypy migrations · guards (5) · `pytest -q` **131 passed, 0 skipped**. Migration 006 up/down/re-up verified.

**Deferred (in scope of the ticket, per its scope line):** anchoring job (MVP-071). Adapter `verify_capability` call sites (send/campaign) land with those adapters (MVP-032+).

**Next:** MVP-025 (outbox emit + publisher, migration 007) → MVP-020 (packs, 008).

## 2026-07-30 — MVP-025 · Outbox emit + publisher

**Ticket:** [MVP-025](../docs/tickets/MVP-025.md) · P0 · deps MVP-024. Branch `feature/mvp-024-audit-chain` (batched with 024).

**Files:** `migrations/versions/a9f45bacd465_007_events.py` (`event_outbox` global + partial index on unpublished); `core/events/topics.py` (`ALLOWED_EVENT_TYPES` mirror of topics.yaml + `stream_name`); `core/events/outbox.py` (`emit` same-txn + NOTIFY, `publish_batch` FOR UPDATE SKIP LOCKED → Redis XADD → mark, `run_publisher` loop, CloudEvents envelope); `tests/unit/test_events_topics.py`; `tests/integration/test_outbox.py`.

**Design:** `emit()` writes in the caller's transaction; `publish_batch()` relays to Redis streams **at least once** (XADD before mark → crash republishes; consumers dedupe at MVP-027). CloudEvents 1.0 envelope `{specversion, id, type, source: gop/{source}, subject: org_id, time, data}`. `ALLOWED_EVENT_TYPES` is an in-repo constant (no runtime docs dependency) drift-tested against topics.yaml; MVP-030 will generate typed models from the same YAML. `event_outbox` is global (DECISIONS 2026-07-30).

**Requirement → evidence (all live, pg+redis):**
| Criterion | Test | Result |
|---|---|---|
| crash between insert and publish → published on restart | `test_crash_between_insert_and_publish_is_published_on_restart` | PASS |
| CloudEvents envelope per topics.yaml | `test_emit_then_publish_delivers_cloudevent` + `test_cloud_event_envelope_shape` | PASS |
| envelope/type registry drift | `test_allowed_types_match_topics_yaml` | PASS |
| idempotent publish | `test_publish_is_idempotent` | PASS |

**Commands:** ruff · mypy core (42) · guards (5) · `pytest -q` **137 passed, 0 skipped**. Migration 007 up/down/re-up verified.

**Deferred:** `run_publisher` loop is wired into the worker/scheduler at MVP-028; LISTEN/NOTIFY wakeup is a latency refinement over the 200ms poll (NOTIFY already emitted). Grafana publisher-lag metric with the observability work.

**Next:** MVP-020 (packs migration 008 + archetype seed) — completes the deferred sequencing behind 024/025.

## 2026-07-30 — MVP-020 · Packs migration 008 + archetype seed

**Ticket:** [MVP-020](../docs/tickets/MVP-020.md) · P0 · deps MVP-016. Branch `feature/mvp-024-audit-chain` (batched with 024/025). Completes the sequencing deferred behind 024/025.

**Files:** `migrations/versions/c7f0c9c41a27_008_packs.py` (packs, pack_installations+RLS, catalog_schemas, agent_archetypes+seed, agent_bindings, agent_instances+RLS); `core/packs/archetypes.py` (`ARCHETYPE_ALLOWLISTS`); `tests/unit/test_archetypes.py`; `tests/integration/test_packs_seed.py`.

**RLS:** `pack_installations` + `agent_instances` org-scoped; `packs`/`catalog_schemas`/`agent_archetypes`/`agent_bindings` global registry (no RLS). **Seed:** 5 archetypes (see DECISIONS 2026-07-30 for the 6-vs-5 note), allowlists byte-for-byte from tool-permissions.yaml, idempotent (ON CONFLICT). Bindings/instances *rows* deferred to the installer (MVP-040).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| allowlists match tool-permissions.yaml byte-for-byte | `test_archetypes::test_constants_match...` + `test_packs_seed::test_seed_matches_constants_byte_for_byte` | PASS |
| seeds idempotent (re-run: no duplicates) | `test_packs_seed::test_reseed_is_idempotent` + re-run `alembic upgrade` | PASS |

**Commands:** ruff · mypy core (43) · guards (5) · `pytest -q` **140 passed, 0 skipped**. Migration 008 up/down/re-up + double-upgrade verified.

**Next:** MVP-021 + MVP-022 (tenant settings + feature flags, migration 009).

## 2026-07-30 — MVP-021 · Tenant settings service · + MVP-022 · Feature flag service

**Tickets:** [MVP-021](../docs/tickets/MVP-021.md) + [MVP-022](../docs/tickets/MVP-022.md) · P0 · deps MVP-020. Branch `feature/mvp-021-022-settings-flags`. Shared migration 009.

**Files:** `migrations/versions/cd8141763357_009_settings_flags.py` (tenant_settings+RLS, feature_flags, flag_rules — all per schema-v2); `core/tenancy/settings.py` + `settings_router.py` (021); `core/tenancy/flags.py` + `flags_router.py` (022); `scripts/flag_debt.py`; tests `test_settings.py`, `test_flags.py`, `test_flags_db.py`.

**MVP-021 (settings):** `resolve()` 4-layer precedence **flag > tenant > pack > platform** with provenance; writes append a new version row (never UPDATE), enforce **tighten-only autonomy** (loosening fails closed until MVP-065 trust ledger), write a `settings.changed` audit entry (integrates MVP-024), and publish `settings:{org}` invalidation; `resolve_at()` walks version history. `POST /v1/settings`, `GET /v1/settings/effective`. Deferred: full JSON-Schema validation vs schema_ref (needs `jsonschema`, BLOCKERS #4).

**MVP-022 (flags):** in-process `eval(snapshot, key, ctx)` — **pure, I/O-free hot path**; precedence user>tenant>pack>global with a **sticky bucket** rollout gate; snapshot swapped atomically (single ref → no torn reads); **fail-closed** kill-switch defaults; boot fallback file (persist/load); `publish_flag_change` pubsub push for ≤2s kill propagation (subscriber loop wired at MVP-028); `GET /v1/flags/eval`; `scripts/flag_debt.py` (expiry-debt CI, fail >20 or any >90d). Deviation: `bucket()` uses SHA-256 not murmur3 (no new dep; deterministic + well-distributed).

**Requirement → evidence (all live):**
| Criterion | Test | Result |
|---|---|---|
| settings provenance (pack\|tenant\|flag) + precedence matrix | `test_settings::test_precedence_flag_over_tenant_over_pack_over_platform` | PASS |
| loosening autonomy rejected | `test_settings::test_tighten_only_rejects_loosening` | PASS |
| resolve_at historical | `test_settings::test_resolve_at_walks_history` | PASS |
| flag eval precedence + no-I/O | `test_flags::test_precedence_user_over_tenant_over_global` (pure fn) | PASS |
| sticky bucket stability | `test_flags::test_bucket_is_stable_and_in_range` | PASS |
| torn-read (atomic snapshot) | `test_flags::test_snapshot_swap_is_atomic_no_torn_read` | PASS |
| kill flag flip observed (reload) | `test_flags_db::test_load_snapshot_and_kill_switch_flip` | PASS |

**Commands:** ruff · mypy core (47) · guards (5) · `flag_debt.py` OK · `pytest -q` **151 passed, 0 skipped**. Migration 009 up/down/re-up verified.

**Deferred (disclosed):** the ≤2s kill-switch subscriber loop + 30s snapshot refresher run in the worker/scheduler (MVP-028); jsonschema settings validation (BLOCKERS #4).

**Next:** MVP-058 (prompts, migration 010) → MVP-023 (CRM, 011); then the Redis-streams consumer set 026–029 + 030.

## 2026-07-30 — MVP-058 · Prompt registry · + MVP-023 · CRM migration

**Tickets:** [MVP-058](../docs/tickets/MVP-058.md) (prompts, migration 010) + [MVP-023](../docs/tickets/MVP-023.md) (CRM, migration 011). Branch `feature/mvp-058-023-prompts-crm`. Both pulled into migration-number order (058 forward per DECISIONS 2026-07-29).

**MVP-058 (prompt registry):** `migrations/…010_prompts.py` — `prompt_layers` (partial RLS: base/vertical global, tenant org-scoped; **content immutable** via BEFORE UPDATE trigger), `prompt_bindings` (+RLS, partial unique index = **one active per (instance,task)**), `prompt_evals` (global). `core/prompts/registry.py` — `create_layer`, `pin_binding` (runs `check_compat` on `requires{}` → **refuses incompatible pins**, deactivate-then-activate in one txn), `get_active_binding`, `revert_to`. Tests: `test_prompt_registry.py` (compat refused + compatible pins; one-active invariant; content immutable). **Deferred:** the internal HTTP registry endpoints are thin wrappers to add when the composer (MVP-059) + eval gate (MVP-096) consume them — the service is complete and tested.

**MVP-023 (CRM):** `migrations/…011_crm.py` — leads, appointments, orders, attributions, segments (**all +RLS**); `leads.last_customer_msg_at` (72h silent-detection source) kept current by an AFTER INSERT trigger on `messages` (inbound only); money as integer minor units (DECISIONS 2026-07-30). Schema-only (rows written by the normalizer/MVP-033 + agent `crm.write`). Tests: `test_crm.py` (5-table RLS isolation under app_rw; trigger updates on inbound, not outbound).

**Requirement → evidence (all live):**
| Criterion | Test | Result |
|---|---|---|
| incompatible pin refused | `test_prompt_registry::test_incompatible_pin_refused...` | PASS |
| one active binding per (instance,task) | `test_prompt_registry::test_one_active_binding...` | PASS |
| layer content immutable | `test_prompt_registry::test_layer_content_is_immutable` | PASS |
| RLS green on all five CRM tables | `test_crm::test_all_five_tables_isolated_under_app_rw` | PASS |
| last_customer_msg_at updates on inbound message | `test_crm::test_inbound_message_updates_lead_last_customer_msg_at` | PASS |

**Commands:** ruff · mypy core (48) · guards (5) · `pytest -q` **156 passed, 0 skipped**. Migrations 010 + 011 up/down/re-up verified; chain linear through 011.

**Next:** the Redis-streams consumer set — MVP-026 (framework) → 027 (dedupe) → 028 (scheduler) → 029 (retries/DLQ) → 030 (typed event catalog).

## 2026-07-30 — MVP-026..030 · Redis-streams consumer set + typed event catalog

**Tickets:** MVP-026 (consumer framework), 027 (dedupe), 028 (scheduler), 029 (retries/DLQ), 030 (typed event catalog). Branch `feature/mvp-026-030-consumers`. No new dependencies; no new migrations (dedupe_consumer already exists from 006).

**Files:** `core/events/consumer.py` (026+027+029), `core/events/scheduler.py` (028), `core/events/types.py` (generated) + `scripts/gen_events.py` (030), `core/events/outbox.py` (payload validation), `scripts/dlq-replay.py` (029); tests `test_consumer.py`, `test_scheduler.py`, `test_event_types.py`.

- **MVP-026 framework** — `@consumer(stream, group)`; `drain_once` = XAUTOCLAIM idle-reclaim + XREADGROUP new; `run_consumer` loop with graceful shutdown (in-flight acks, no loss). First consumer: a no-op logger on msg.received.
- **MVP-027 dedupe** — every message runs through `INSERT dedupe_consumer (consumer, event_id) ON CONFLICT DO NOTHING`; handler runs only on first sight → exactly-once effect; `prune_dedupe` removes rows > 30d.
- **MVP-028 scheduler** — 5-field cron matcher (`cron_matches`), timezone-aware (`zoneinfo`) so tenant-local schedules fire at local time; per-(job,minute) Redis lock → one fire across schedulers. (croniter avoided, jobs_runs deferred — DECISIONS.)
- **MVP-029 retries/DLQ** — bounded retries; after 5 (6th failure) → `gop:dlq:<type>` with error history + `alert.ops` emit; `scripts/dlq-replay.py` re-injects after a fix.
- **MVP-030 typed catalog** — `scripts/gen_events.py` generates `core/events/types.py` (payload specs + checksum) from topics.yaml; drift test fails CI if stale; `emit()` rejects malformed payloads.

**Requirement → evidence (all live, pg+redis):**
| Criterion | Test | Result |
|---|---|---|
| kill mid-handler → single redelivery | `test_consumer::test_crashed_message_is_reclaimed_once` | PASS |
| graceful shutdown, no ack loss | `test_consumer::test_run_consumer_graceful_stop` | PASS |
| duplicate delivery → one effect + 30d prune | `test_consumer::test_handle_ack_and_dedupe`, `test_prune_dedupe_removes_old_rows` | PASS |
| two schedulers → each job fires once | `test_scheduler::test_two_schedulers_fire_a_job_once` | PASS |
| tenant-local firing | `test_scheduler::test_tenant_local_firing` | PASS |
| 6th failure → DLQ + error history; replay | `test_consumer::test_poison_message_dead_letters_and_replays` | PASS |
| CI fails on topics.yaml/model drift; emit rejects malformed | `test_event_types::*` + `gen_events.py --check` | PASS |

**Commands:** ruff · mypy core (51) + migrations · guards (5) · `gen_events.py --check` OK · `pytest -q` **168 passed, 0 skipped**.

**🎯 The MVP-012..030 goal is complete — 19/19 tickets, all verified live.**

## 2026-07-30 — MVP-032 · Webhook ingress · + MVP-033 · Message normalizer (WhatsApp inbound path)

**Tickets:** [MVP-032](../docs/tickets/MVP-032.md) + [MVP-033](../docs/tickets/MVP-033.md) · P0. Branch `feature/mvp-031-037-whatsapp`. The "customer inquiry enters through a conversation channel" step of the MVP workflow, built end-to-end (no real Meta — DECISIONS 2026-07-30).

**Files:** `core/channels/whatsapp/ingress.py` (032) + `normalizer.py` (033); `migrations/versions/126c955c13de_channel_resolve_fn.py` (resolve_channel SECURITY DEFINER); `core/common/config.py` (whatsapp_app_secret/verify_token); `core/api/main.py` (mount); tests `test_whatsapp_ingress.py`, `test_whatsapp_normalizer.py`.

**MVP-032 ingress** — public `GET/POST /webhooks/whatsapp`: verify-token handshake; **constant-time HMAC-SHA256** signature check (`hmac.compare_digest`); dedupe by wamid (webhook_events `(provider, external_id)` unique); malformed body → **quarantine row + 200** (never 5xx → no Meta retry-storm).

**MVP-033 normalizer** — `normalize_pending()` drains unprocessed `webhook_events`, resolves org from phone_number_id via `resolve_channel` (RLS-exempt), sets tenant context, upserts contact + open conversation, inserts the inbound message (its trigger updates `leads.last_customer_msg_at`), emits `msg.received.v1` via the outbox, marks the webhook processed — one transaction per event. Idempotent (wamid unique).

**Requirement → evidence (all live):**
| Criterion | Test | Result |
|---|---|---|
| invalid signature → 403, no row | `test_whatsapp_ingress::test_invalid_signature_rejected_no_row` | PASS |
| duplicate external_id → single row | `test_whatsapp_ingress::test_valid_signature_persists_and_dedupes` | PASS |
| malformed JSON → 200 + quarantine | `test_whatsapp_ingress::test_malformed_body_is_quarantined_with_200` | PASS |
| verify-token handshake | `test_whatsapp_ingress::test_verify_handshake` | PASS |
| webhook → contact/conversation/message + msg.received emitted, idempotent | `test_whatsapp_normalizer::test_normalizes_message_and_emits_event` | PASS |
| unknown channel handled (no orphan contact) | `test_whatsapp_normalizer::test_unknown_channel_is_marked_processed_without_contact` | PASS |

**Commands:** ruff · mypy core (53) + migrations · guards (5) · `pytest -q` **174 passed, 0 skipped**. Migration round-trip verified.

**Deferred:** creds-never-logged scrub test (031 connect) + the p95<50ms soak — connect/soak land with MVP-031; gated real-send stays blocked (#3). Remaining WhatsApp: 031 (connect), 034 (send, gated), 035/036/037.

## 2026-07-30 — MVP-031 · WhatsApp WABA connect (channel onboarding + encrypted credentials)

**Ticket:** [MVP-031](../docs/tickets/MVP-031.md) · P0. Branch `feature/mvp-031-whatsapp-connect` (off main). Lets an owner attach their WhatsApp Business number so the inbound path (032/033) and the coming send adapter (034) have a channel + credential to work with. No real Meta calls — the client runs **simulated** until `whatsapp_live_enabled` (§10.4 / BLOCKERS #3); the real httpx paths are written so flipping the flag is the only change.

**Files (new):** `core/common/crypto.py` (Fernet encrypt/decrypt for creds at rest), `core/channels/whatsapp/meta_client.py` (gated Meta client: verify_credentials / register_webhook / echo_test / send_text), `core/channels/whatsapp/credentials.py` (store/load encrypted creds), `core/channels/whatsapp/connect.py` (connect + health endpoints), `migrations/versions/cfd462c65ec9_channel_credentials.py`, `tests/integration/test_whatsapp_connect.py`.
**Files (modified):** `core/common/config.py` (`whatsapp_live_enabled`, `credential_encryption_key` + `_DEV_CREDENTIAL_KEY`), `core/api/main.py` (mount connect router).

**Migration `cfd462c65ec9`** (revises `126c955c13de`): `channel_credentials` (channel_id PK → channels ON DELETE CASCADE, org_id, ciphertext, timestamps), **RLS applied**. Upgrade→downgrade→upgrade round-trip verified; `relforcerowsecurity=true`.

**Connect flow** — `POST /v1/channels/whatsapp/connect` (owner, `ORG_MANAGE`): (1) token gate `verify_credentials` → else **400 invalid_token**; (2) handshake gate `register_webhook` → else **403 handshake_failed**; (3) echo gate `echo_test` → else **200 {connected:false, reason:echo_failed}** (nothing persisted). All pass → cross-org check via `resolve_channel` (number owned elsewhere → **409**), else insert/update `channels` (active) + `store_credentials` (Fernet ciphertext). `GET /.../{channel_id}/health` re-runs the echo probe with stored creds; unknown/other-org → 404 (RLS-scoped).

**Security:** access token encrypted at rest (never plaintext in DB), never logged (scrub test), never returned in a response. Credential store is org-scoped + RLS; cross-org number takeover rejected.

**Requirement → evidence (all live, pg):**
| Criterion | Test | Result |
|---|---|---|
| valid connect → channel active + credential encrypted at rest | `test_whatsapp_connect::test_connect_success_persists_channel_and_encrypted_credential` | PASS |
| bad token → 400, no row | `test_whatsapp_connect::test_bad_token_returns_400_and_writes_no_row` | PASS |
| handshake mismatch → 403, no row | `test_whatsapp_connect::test_handshake_mismatch_returns_403_and_writes_no_row` | PASS |
| echo failure → connected=false, no row | `test_whatsapp_connect::test_echo_failure_reports_not_connected_and_writes_no_row` | PASS |
| credentials never logged | `test_whatsapp_connect::test_credentials_never_logged` | PASS |
| reconnect same org → update in place (rotates cred) | `test_whatsapp_connect::test_reconnect_same_org_updates_in_place` | PASS |
| number owned by another org → 409 | `test_whatsapp_connect::test_number_owned_by_another_org_is_rejected` | PASS |
| health probe (healthy + 404 unknown) | `test_whatsapp_connect::test_health_probe` | PASS |

**Commands:** ruff (all pass) · mypy core (57) + migrations (3) · guards (5) · `pytest -q` **183 passed, 0 skipped** · migration round-trip OK.

**Deferred:** real Meta echo against a test number stays gated (#3) — verified in simulated mode; going-live flips `whatsapp_live_enabled` once API access lands. Next: MVP-034 (gated send adapter).

**Commit `2160890`** on `feature/mvp-031-whatsapp-connect`; **merged to main `644b334` and pushed** (`a884700..644b334`) 2026-07-30.

## 2026-07-30 — MVP-034 · Gated send adapter (single outbound exit) · + MVP-036 enforcement (folded in)

**Ticket:** [MVP-034](../docs/tickets/MVP-034.md) · P0. Branch `feature/mvp-034-036-send-adapter` (off main). The one function customer-facing text leaves through — refuses anything unaudited, untokened, suppressed, or without consent. Meta calls stay **gated-simulated** (`whatsapp_live_enabled=False`, §10.4 / BLOCKERS #3).

**Files (new):** `core/channels/whatsapp/send.py`, `tests/integration/test_whatsapp_send.py`. **No migration** — the messaging schema (005) already carries `contacts.consent_status`, `suppressions`, and `messages.audit_id/status/provider_message_id`.

**Four gates (fail-closed, before any external call):**
1. **audit capability** — `verify_capability(audit_id, action="msg.send", resource=conversation_id)`; missing/expired/mismatched → refuse `approval_required`.
2. **execution token** — interim non-empty check (real one-time binding to the capability lands MVP-066); empty/None → `approval_required`.
3. **suppression** — `suppressions` join; `all` scope blocks everything, `marketing` blocks marketing; **any lookup error fails closed** → `suppressed_contact`.
4. **consent** — marketing requires `consent_status ∈ {opted_in, granted}`; transactional class is exempt → `consent_missing`.

**On pass:** insert `messages` (direction=outbound, status=queued, audit_id) → send via gated `MetaClient` with bounded retries (429 honours Retry-After; 5xx retried ×3) → success: `status=sent` + `provider_message_id`, emit `msg.sent.v1`, `write_outcome(succeeded)`; exhausted failure: `status=failed`, emit `msg.failed.v1`, `write_outcome(failed)`. Two transactions (durable queued row, then outcome) so the intent is recorded even if the send crashes.

**MVP-036 (enforcement half) folded in:** the suppression+consent join *is* this gate. Remaining 036 — STOP/UNSUB keyword auto-suppress in the normalizer (en/hi/te), the transactional confirmation reply, suppressed badge — is **not** in this ticket; the auto-confirm reply is an unapproved automated send (§19) held for a founder decision.

**Requirement → evidence (all live, pg):**
| Criterion | Test | Result |
|---|---|---|
| success → msg.sent.v1 + audit outcome + provider id stored | `test_whatsapp_send::test_success_emits_sent_and_records_outcome` | PASS |
| missing audit_id → refusal, no Meta call | `test_whatsapp_send::test_missing_audit_id_refused_no_http` | PASS |
| bad/empty token → refusal, no Meta call | `test_whatsapp_send::test_bad_token_refused_no_http` | PASS |
| suppressed marketing blocked, transactional allowed | `test_whatsapp_send::test_suppressed_marketing_blocked_transactional_allowed` | PASS |
| consent unknown: marketing blocked, transactional allowed | `test_whatsapp_send::test_consent_unknown_marketing_blocked_transactional_allowed` | PASS |
| 429 Retry-After honoured | `test_whatsapp_send::test_429_retry_after_is_honored` | PASS |
| 5xx retried ×3 → msg.failed.v1 + status failed | `test_whatsapp_send::test_5xx_retried_thrice_then_failed` | PASS |
| suppression lookup error fails closed | `test_whatsapp_send::test_suppression_lookup_error_fails_closed` | PASS |

**Commands:** ruff (pass) · mypy core (58) (pass) · guards (5, pass — send.py is inside the allowed `core/channels/` adapter dir) · `pytest -q` **191 passed, 0 skipped**.

**Deferred:** real Meta send stays gated (#3); execution-token real binding (MVP-066); ledger/figure-refs gate (MVP-054, param accepted but not enforced); 036 keyword net + confirm + badge (pending founder decision on auto-send).

## 2026-07-30 — MVP-036 · Opt-out keyword net (STOP auto-suppress + transactional confirm)

**Ticket:** [MVP-036](../docs/tickets/MVP-036.md) · P0. Branch `feature/mvp-036-stop-keywords` (off main). The enforcement half (suppression+consent join) already shipped in MVP-034; this adds the inbound STOP/UNSUB keyword net + auto-suppress + the founder-approved transactional confirmation.

**Files (new):** `core/channels/whatsapp/keywords.py`, `tests/integration/test_whatsapp_stop.py`. **Files (modified):** `core/channels/whatsapp/normalizer.py`. **No migration** (`suppressions` PK `(org_id,contact_id,scope)` already gives idempotent auto-suppress).

**Keyword net** — `keywords.py`: platform list `stop / unsubscribe / unsub / band karo / bandh karo / ఆపండి`. Whole-message strict match (normalize = lower-case + **ASCII-only** punctuation strip + whitespace collapse), so "STOP!", "Band Karo.", "ఆపండి" match while "I couldn't stop thinking about the ring" and "stopwatch" do not. ASCII-only stripping was required — a Unicode `[^\w\s]` strip corrupted Telugu combining marks and broke the non-Latin keyword (caught by the parametrised test). Pack-extensible later.

**Normalizer wiring** — on an inbound STOP: insert `suppressions (scope=marketing) ON CONFLICT DO NOTHING RETURNING` (idempotent); if newly suppressed, queue a confirmation. Confirmations are sent **after the event transaction commits** (suppression durable first), each via `_mint_send_capability` (an audit `msg.send` capability, actor_type=system) → gated `send(message_class="transactional", body=STOP_CONFIRM_TEXT)`. Fixed platform text only (no model content). A `SendRefused` (e.g. channel not connected) is logged, never crashes the batch.

**§19 decision:** auto-sending the confirmation without human approval is a founder-approved narrow exception (DECISIONS 2026-07-30) — keyword-triggered only, fixed text, transactional class, one per STOP, fully audited, gated-simulated until go-live.

**Requirement → evidence (all live, pg):**
| Criterion | Test | Result |
|---|---|---|
| keyword matrix incl. STOP / unsubscribe / band karo / ఆపండి (+ negatives) | `test_whatsapp_stop::test_keyword_matches`, `::test_keyword_does_not_match` | PASS |
| STOP suppresses within one message + transactional confirm sent (msg.sent.v1) | `test_whatsapp_stop::test_stop_suppresses_and_confirms_once` | PASS |
| suppression idempotent → confirm only on first STOP | `test_whatsapp_stop::test_repeated_exact_stop_does_not_double_confirm` | PASS |
| suppressed contact: marketing blocked, transactional allowed | `test_whatsapp_send::test_suppressed_marketing_blocked_transactional_allowed` (MVP-034) | PASS |
| consent=unknown + marketing blocked | `test_whatsapp_send::test_consent_unknown_marketing_blocked_transactional_allowed` (MVP-034) | PASS |

**Commands:** ruff (pass) · mypy core (59) (pass) · guards (5, pass) · `pytest -q` **206 passed, 0 skipped**. App imports clean (no normalizer↔send cycle).

**Deferred:** suppressed badge in chats (frontend, MVP-087); real send gated (#3); fuzzy/pack-extensible keyword matching later.

## 2026-07-31 — MVP-035 · WhatsApp templates management (registry, Meta status sync, send gate)

**Ticket:** [MVP-035](../docs/tickets/MVP-035.md) · P1. Branch `feature/mvp-035-templates` (off main). Meta requires pre-approved templates for business-initiated messages; this manages the registry, syncs review status, and gates sends. Founder chose **full scope** incl. webhook-driven status updates. Real Meta submit/webhooks stay gated (#3).

**Files (new):** `core/channels/whatsapp/templates.py`, `verticals/jewelry/templates/whatsapp.yaml` (jewelry_v2 seed), `scripts/seed_whatsapp_templates.py` (gated), `tests/integration/test_whatsapp_templates.py`, migration `83efabba79ee_message_templates_meta.py`.
**Files (modified):** `core/channels/whatsapp/meta_client.py` (submit_template + send_template, gated; `_send_result` helper), `core/channels/whatsapp/send.py` (optional `template=(key,lang)` path + gate), `core/channels/whatsapp/connect.py` (set `waba_id` on connect + `GET /templates`), `core/channels/whatsapp/normalizer.py` (skip status webhooks).

**Migration `83efabba79ee`** (revises `cfd462c65ec9`): additive — `message_templates` += `category, namespace, provider_template_id, provider_reason, updated_at`; `channels` += `waba_id`; new `resolve_channel_by_waba(text)` SECURITY DEFINER (org resolution for status webhooks, keyed by WABA id, before tenant context — same pattern as `resolve_channel`). Upgrade→downgrade→upgrade verified; roles re-applied.

**templates.py** — `upsert_template` (edit resets to draft), `list_templates`, `get_template`, `submit_template` (gated → pending + provider id), `apply_status_update` (APPROVED/REJECTED/PENDING/PAUSED/DISABLED → status + reason), `assert_template_sendable` (raises `TemplateNotSendable` naming the template), `seed_from_manifest`, and `process_template_status_pending` (drains `message_template_status_update` webhooks; normalizer now skips them via a SQL field filter so it can't swallow them).

**Send gate** — `send(..., template=(key, language))` calls `assert_template_sendable` inside the gate txn and sends via `meta_client.send_template`; a rejected template is refused before any Meta call. Freeform (`template=None`) is unchanged (MVP-034 tests still green).

**Rule Zero** — all jewelry template *content* is declarative in `verticals/jewelry/templates/whatsapp.yaml`; `core/` never names the pack (guard caught an early docstring and was fixed).

**Requirement → evidence (all live, pg):**
| Criterion | Test | Result |
|---|---|---|
| status sync approved/rejected transitions reflected | `test_whatsapp_templates::test_lifecycle_draft_submit_approve_reject` | PASS |
| rejected template blocks send, actionable problem naming it | `::test_gate_blocks_non_approved_naming_template` | PASS |
| send with rejected template → refused, no Meta call | `::test_send_with_rejected_template_refused_no_meta_call` | PASS |
| approved template → template send path used | `::test_send_with_approved_template_uses_template_path` | PASS |
| status webhook applied (org by WABA id); normalizer skips it | `::test_status_webhook_drainer_and_normalizer_skips` | PASS |
| seed manifest upserts; cross-org isolation | `::test_seed_from_manifest_and_isolation` | PASS |
| list endpoint returns org templates | `::test_list_endpoint_returns_org_templates` | PASS |
| jewelry_v2 seed has all 5 templates, valid | `::test_jewelry_pack_seed_manifest_is_valid` | PASS |

**Commands:** ruff (pass) · mypy core (60) + migrations (3) (pass) · guards (5, pass) · `pytest -q` **214 passed, 0 skipped** · migration round-trip OK · route `GET /v1/channels/whatsapp/templates` registered.

**Deferred:** template-builder UI + campaign-wizard picker (frontend, MVP-08x); real Meta submission + status webhooks (gated #3 — `scripts/seed_whatsapp_templates.py` submits simulated until go-live).

## 2026-07-31 — MVP-037 · WhatsApp media handling (gated download → fail-closed AV → store → link)

**Ticket:** [MVP-037](../docs/tickets/MVP-037.md) · P1. Branch `feature/mvp-037-media` (off main). Customers send photos; agents send catalog images. Built with **simulated** AV + storage adapters (no new deps, §9 — founder chose "Simulated adapters" 2026-07-31); real clamav/MinIO deferred (BLOCKERS #12). Meta media I/O gated (#3).

**Files (new):** `core/channels/whatsapp/media.py`, `tests/integration/test_whatsapp_media.py`. **Files (modified):** `core/channels/whatsapp/meta_client.py` (gated `download_media`/`upload_media`), `core/channels/whatsapp/normalizer.py` (media ingest wiring), `core/common/config.py` (`media_av_enabled`/`media_storage_enabled`, both default False). **No migration** (`messages.media` jsonb already exists).

**media.py** — `ALLOWED_MIME` (jpeg/png/webp/pdf) + `MAX_MEDIA_BYTES` (16MB) gates; `ingest_inbound_media` = mime gate → download (gated) → size gate → **AV scan fail-closed** (`MediaScanError` → `quarantined`, never stored) → infected → `infected` → clean → store + sha256 → `MediaDescriptor` (never raises; status says what happened). `MediaScanner`/`MediaStore` Protocols with `SimulatedScanner`/`SimulatedStore` defaults; `default_scanner`/`default_store` raise `NotImplementedError` if `media_*_enabled` is set (real adapter absent) so a no-op AV scanner can't silently run in prod. `upload_outbound_media` gates then uploads (gated).

**Normalizer** — after inserting an inbound message, `_ingest_media` downloads/scans/stores any attachment, writes the descriptor to `messages.media`, emits `alert.ops.v1` on quarantine, and includes the descriptor in `msg.received.v1`. A disallowed mime or missing credential still normalizes the message (text fallback body, e.g. `[image]`).

**Requirement → evidence (all live where DB-backed):**
| Criterion | Test | Result |
|---|---|---|
| disallowed mime rejected (no download) | `test_whatsapp_media::test_disallowed_mime_rejected_without_download` | PASS |
| oversize rejected at cap | `::test_oversize_rejected_at_cap` | PASS |
| scanner down → quarantined, fail-closed (not stored) | `::test_scanner_error_quarantines_fail_closed` | PASS |
| infected → rejected | `::test_infected_rejected` | PASS |
| clean media stored (bytes persisted, sha256) | `::test_clean_media_stored` | PASS |
| outbound upload gates + uploads | `::test_outbound_upload_gates_and_uploads` | PASS |
| enabling real adapters fails closed | `::test_enabling_real_adapters_fails_closed` | PASS |
| e2e image stored + linked + media on event | `::test_image_message_stored_and_linked` | PASS |
| disallowed mime → message still normalized (text fallback) | `::test_disallowed_mime_still_normalizes` | PASS |
| scanner down e2e → quarantine + alert.ops.v1 | `::test_scanner_down_quarantines_and_alerts` | PASS |

**Commands:** ruff (pass) · mypy core (61) (pass) · guards (5, pass — a "Jewelry" comment in core was caught and reworded) · `pytest -q` **225 passed, 0 skipped**.

**Deferred (BLOCKERS #12):** real clamav + MinIO/S3 clients as deps + docker-compose services + wiring behind the two flags; media rendering in the chat transcript (frontend); Meta media download/upload gated (#3).

**🎯 WhatsApp channel group (MVP-031–037) complete:** connect, ingress + signature verify, normalize, send (4 gates + retries), templates + status sync, opt-out compliance, media — end-to-end, gated-simulated for all real Meta I/O.

## 2026-07-31 — MVP-038 · Pack contract models (typed L0↔L1 contracts)

**Ticket:** [MVP-038](../docs/tickets/MVP-038.md) · P0 · "S". Branch `feature/mvp-038-pack-contracts` (off main). First ticket of the pack subsystem. Founder chose **full scope** (also model onboarding/ui/calendar/evals so every verticals/* file parses).

**Files (new):** `core/packs/contracts.py` (pure pydantic, zero I/O), `tests/unit/test_pack_contracts.py`. No DB, no API, no deps (pydantic already present). No migration.

**Contracts** (per docs/21-platform/core-platform.md §40–92): `PackManifest` (+`SlotSpec`/`PackRequires`/`PackProvides`/`Signing`), `AgentBinding` (+`TaskDef`/`PromptLayerRef`/`ToolGrant`/`PolicyRuleRef`), `BindingsPack`, `CatalogSchema` (+`from_document`), `PricingStrategyDef`, `WorkflowDef`, `IntegrationSpec`, `OnboardingPack`, `UiPack`, `CalendarPack`, `EvalSuite`, plus `CompliancePack`/`KPIPack`/`PromptLayerDef` (no file yet — for later tickets). **Strict** (`extra=forbid`) where the platform owns the shape (manifest, bindings core, catalog, workflow, calendar); **open** where the pack/engine does (integrations, pricing rules, onboarding, ui, evals).

**Doc-vs-data deviations** (§4, DECISIONS 2026-07-31): modelled the pack data (`kpis`+`budgets` not `kpi_defs`; `rules`/`rate_sources` not `rule_schema`/`rate_source_requirements`; `identity_keys` is a list of composite key-lists; `mcp_server` may be a bare string; catalog file is a JSON-Schema-plus split by `from_document`) so every file parses.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| every docs/verticals/* contract file parses (both packs) | `test_pack_contracts::test_every_pack_file_parses` (parametrized over all files) | PASS |
| manifests validate; slots present | `::test_manifest_fields` | PASS |
| bindings + catalog schema validate | `::test_bindings_and_catalog` | PASS |
| one wrong field → path-precise error | `::test_unknown_manifest_field_is_path_precise`, `::test_bad_binding_tier_names_the_path`, `::test_workflow_missing_required_field`, `::test_pricing_engine_enum_enforced` | PASS |

**Commands:** ruff (pass) · mypy core (62) (pass) · guards (5, pass) · `pytest -q` **266 passed, 0 skipped**.

**Deferred:** prompt `.md` anchor-splitting into `PromptLayerDef` records (MVP-039); `templates/` seed is MVP-035 (excluded from the contract walk). Signing verify is dev-mode-first (MVP-039).

## 2026-07-31 — MVP-039 · Bundle parser + verifier (dev directory parse + prompt split + digest/ed25519 verify)

**Ticket:** [MVP-039](../docs/tickets/MVP-039.md) · P0 · "M". Branch `feature/mvp-039-bundle-parser` (off main). Dev-mode-first: install from a directory; prod requires a signed bundle.

**Files (new):** `core/packs/bundle.py`, `tests/unit/test_pack_bundle.py`. **Files (modified):** `core/common/config.py` (`packs_dev_mode`, default True). No DB, no migration, **no new deps** (ed25519 via the existing cryptography dependency).

**bundle.py** — `split_prompt_layers` (each `## <a id="x"></a>Layer: archetype.pack.task` heading → a `PromptLayerDef`; content = the following fenced block; version from the file header `vX.Y`; `requires` from the "Composes on `base…`" line). `parse_pack_dir` validates pack.yaml / bindings / catalog (`from_document`) / pricing / workflows / integrations / evals / onboarding / ui / calendar against the MVP-038 contracts and splits all prompts, wrapping any pydantic error with the offending **file path** (`agents/bindings.yaml: …`). `compute_manifest`/`verify_manifest` (sha256 per file; tampered/incomplete tree → `BundleError`), `serialize_manifest`, `verify_signature` (ed25519), and `load_bundle` (dev = parse dir; prod = require MANIFEST + matching digests + valid signature first).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| prompt anchors → layer records incl. versions (concierge.md → 4) | `test_pack_bundle::test_concierge_anchor_split_yields_four_versioned_layers`, `::test_prompt_files_split_to_expected_counts` | PASS |
| tampered digest refused | `::test_verify_manifest_refuses_tampered_digest`, `::test_prod_mode_requires_valid_signature` | PASS |
| one invalid field in bindings.yaml → path-precise error mentioning the file | `::test_invalid_field_in_bindings_names_the_file` | PASS |
| both packs parse to a ParsedPack (expected counts) | `::test_parse_jewelry_pack_dir`, `::test_parse_kirana_pack_dir` | PASS |
| ed25519 sign/verify (tamper + wrong key → false) | `::test_ed25519_signature_roundtrip` | PASS |

**Commands:** ruff (pass) · mypy core (63) (pass) · guards (5, pass) · `pytest -q` **279 passed, 0 skipped**.

**Deferred:** `.tar.zst` bundle packing/unpacking (needs `zstandard` dep — BLOCKERS #13; verification is over the tree so no acceptance criterion is affected); publisher keys + the signing tool are explicitly out of MVP-039 scope; the real platform public key is config/secret for prod.

## 2026-07-31 — MVP-040 · Transactional installer + API (the L-effort pack install pipeline)

**Ticket:** [MVP-040](../docs/tickets/MVP-040.md) · P0 · "L". Branch `feature/mvp-040-installer` (off main). Founder chose **Option A** (build installer, defer policies/workflows steps whose tables aren't built — 2026-07-31).

**Files (new):** `core/packs/installer.py`, `core/packs/router.py`, `migrations/versions/5dcbda42efca_pack_installation_failed_status.py`, `tests/integration/test_pack_installer.py`. **Files (modified):** `core/api/main.py` (mount packs router).

**Migration `5dcbda42efca`** (revises `83efabba79ee`): additive — adds `'failed'` to `pack_installations.status` CHECK so a rolled-back install is distinguishable from one still `installing`. Round-trip verified.

**Installer** — `install(org_id, pack_dir, config)`: `load_bundle` (verify + parse) → get-or-create `packs` row → **digest idempotency** (an existing `active` install of the same bundle digest → no-op) → create `pack_installations` (`installing`) → **single tenant-scoped txn** running the 6 steps → `active` + `pack.installed` audit. On any step failure the txn rolls back (zero partial artifact rows) and a separate txn marks the install `failed` + records `_error_step`. **Steps:** 1 catalog schema → `catalog_schemas`; 2 pack-migrations (none); 3 prompt layers → `prompt_layers` (candidate, pack-global, NOT active); 4 **policies** (deferred no-op, #14); 5 **workflows** (deferred no-op, #14); 6 bindings → `agent_bindings` (upsert) + paused `agent_instances` (unseeded `support` archetype skipped). `uninstall`: re-pause the org's instances, mark `uninstalled`, retain the catalog schema, leave L3 untouched (attribute freeze + cred revocation deferred, #14). `list_packs`: published registry.
**API** — `GET /v1/packs` (owner), `POST /v1/packs/installations` (owner, 201), `DELETE /v1/packs/installations/{id}` (owner, 204).

**Requirement → evidence (all live, pg):**
| Criterion | Test | Result |
|---|---|---|
| install → paused instances + candidate layers + schema + bindings + audit | `test_pack_installer::test_install_seeds_paused_instances_and_candidate_layers` | PASS |
| failure at each of the 6 steps → zero partial rows + install failed at that step | `::test_failure_at_each_step_rolls_back_fully` (parametrized ×6) | PASS |
| reinstall same digest → no-op fast path (no dupes) | `::test_reinstall_same_digest_is_noop` | PASS |
| uninstall: instances re-paused, schema retained, L3 untouched | `::test_uninstall_pauses_instances_retains_schema_and_l3` | PASS |
| GET /v1/packs lists published | `::test_list_packs_returns_published` | PASS |

**Commands:** ruff (pass) · mypy core (65) + migrations (3) (pass) · guards (5, pass) · `pytest -q` **289 passed, 0 skipped** · migration round-trip OK · routes `/v1/packs(/installations)` registered.

**Deferred (BLOCKERS #14):** policies/workflows seeding step functions (MVP-044, once 014/016 land) — the installer already calls the no-op hooks; uninstall attribute-freeze (MVP-045, catalog_items/012) + credential revocation; upgrade orchestration (post-MVP, out of scope).

## 2026-07-31 — MVP-041 · Jewelry install e2e fixture (reference install as a CI check)

**Ticket:** [MVP-041](../docs/tickets/MVP-041.md) · P0 · "S". Branch `feature/mvp-041-jewelry-install-e2e` (off main). CI fixture + assertions only, **no production code**.

**Files (new):** `verticals/jewelry/install.yaml` (reference install — pack_ref + config slot values + `expected_result`), `tests/e2e/test_jewelry_install.py`. **Files (modified):** `.github/workflows/ci.yml` (the `migrate` job now creates the `app_rw` role and runs the reference install e2e as a required check).

**e2e** — a fresh org installs jewelry from `install.yaml`'s config and the test asserts the `expected_result` field-by-field: status `active`, **4 paused** `agent_instances` (support archetype unseeded → skipped), catalog schema **version 2**, **9 candidate** prompt layers, **4 bindings**, deferred steps `[policies, workflows]`, and install completes in **<60s** (locally ~0.5s). `install.yaml` sits at the pack root; the contract walk + `parse_pack_dir` ignore it (not a known contract file), it's only included in the bundle digest.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| expected_result asserted field-by-field | `test_jewelry_install::test_reference_jewelry_install_matches_expected_result` | PASS |
| install completes <60s | same test (elapsed assert) | PASS |
| green in CI as a required check | wired: `ci.yml` `migrate` job (app_rw + e2e) | wired (CI run unverified here — MVP-003 CI) |

**Commands:** ruff (pass) · guards (5, pass) · `pytest -q` **290 passed, 0 skipped**.

**Deferred:** `indexes_queued` assertion (MVP-042 index generation); broader integration-test CI wiring (needs redis service etc.) remains a pre-existing gap (MVP-003, 🟡) — only the DB-only reference install is wired here.

## 2026-07-31 — MVP-043 · Kirana dry-run CI gate (second pack installs with zero core changes)

**Ticket:** [MVP-043](../docs/tickets/MVP-043.md) · P0 · "S". Branch `feature/mvp-043-kirana-dryrun` (off main). The architecture guarantee (IDL-005): a second pack must install forever with zero core diffs.

**Files (new):** `verticals/kirana/install.yaml` (expected_plan), `tests/e2e/test_kirana_dryrun.py`. **Files (modified):** `core/packs/installer.py` (`dry_run` + `InstallPlan`), `.github/workflows/ci.yml` (kirana dry-run runs beside the jewelry e2e in the `migrate` job).

**dry_run** — `dry_run(org_id, pack_dir)` loads+validates the bundle (raises `BundleError` on any contract violation), then runs the **full** 6-step pipeline inside an `org_scoped_session` transaction and raises an internal `_DryRunRollback` carrying the `InstallPlan` — so the transaction always rolls back and **nothing is persisted**, while still exercising every step (any core hardcoding that broke a second pack would fail here). The plan reports the counts that would be written.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| dry-run green on clean main (plan matches: 3 bindings/instances, 5 layers, schema v1, 2 workflows/integrations) | `test_kirana_dryrun::test_kirana_dry_run_matches_plan_and_writes_nothing` | PASS |
| dry-run writes nothing (no kirana pack row, no instances) | same test | PASS |
| CI required check | wired: `ci.yml` `migrate` job runs the kirana dry-run | wired (CI run unverified here) |
| red on the proving-hardcoding branch | the dry-run runs the full pipeline → a core hardcode breaking kirana fails it | by construction (proving branch is a founder-maintained fixture) |

**Commands:** ruff (pass) · mypy core (65) (pass) · guards (5, pass) · `pytest -q` **291 passed, 0 skipped**.

**Note:** the permanently-red "proving hardcoding" branch is a CI regression fixture the founder maintains; not created here.

## 2026-07-31 — MVP-039 follow-up · Signed-bundle .tar.zst transport (resolves BLOCKERS #13)

Founder approved adding `zstandard` (§9). `core/packs/bundle.py` now has `pack_bundle` (pack tree + `MANIFEST.sha256` + ed25519 `MANIFEST.sig` → `.tar.zst`) and `unpack_bundle` (decompress with a 50MB `frame_content_size` cap + `tarfile` `data` filter → no path traversal / device files); `load_bundle` transparently unpacks a `.tar.zst` to a temp dir, then verifies + parses. **Files:** `pyproject.toml` (+zstandard 0.25), `core/packs/bundle.py`, `tests/unit/test_pack_bundle.py` (+4: round-trip preserves tree, dev-mode load from .zst, prod signed-load + wrong-key refused, size-cap refused). ruff/mypy/guards pass · `pytest -q` **295 passed**.

## 2026-07-31 — MVP-037 follow-up · Real media adapters: ClamAV + MinIO/S3 (BLOCKERS #12)

Founder approved adding `clamd` + `boto3` (§9). `core/channels/whatsapp/media.py`: `ClamavScanner` (scans via a clamd socket; any failure raises `MediaScanError` → the caller quarantines, fail-closed) and `S3Store` (boto3 to an S3-compatible endpoint — MinIO in dev, S3 in prod; creates the bucket if absent). `default_scanner`/`default_store` now return the real adapters when `media_av_enabled`/`media_storage_enabled` are set (previously raised NotImplementedError). **Files:** `pyproject.toml` (+clamd, +boto3, +mypy overrides), `core/common/config.py` (clamav_host/port + s3_* settings), `infra/docker/docker-compose.dev.yml` (opt-in `media` profile: `clamav` + `minio`), `core/channels/whatsapp/media.py`, `tests/integration/test_media_adapters.py` (+5, skip-if-port-down), `tests/integration/test_whatsapp_media.py` (adapter-selection test updated). ruff/mypy/guards pass · `pytest -q` **296 passed, 4 skipped** (live clamav/minio tests skip — images didn't pull here; see BLOCKERS #12). Code is complete + typed + fail-closed-tested; live scan/store execution is pending the image pull (a Docker credential-helper issue on this Mac).

## 2026-07-31 — Media adapters live-verified + clamav amd64 platform pin (BLOCKERS #12 closed)

Brought up `clamav` + `minio` (`docker compose --profile media up`) and verified the real adapters end-to-end: **all 5** `tests/integration/test_media_adapters.py` pass — ClamAV detects the EICAR test signature and passes clean bytes, MinIO stores + returns bytes, full ingest → stored, scanner error → quarantined. Full suite **300 passed, 0 skipped**. ClamAV ships no arm64 image, so `infra/docker/docker-compose.dev.yml` pins the clamav service `platform: linux/amd64` (emulated on Apple-Silicon dev, native on amd64 servers). BLOCKERS #12 closed.

## 2026-07-31 — MVP-045 · Catalog migration + CRUD (storage, history, dedup)

**Ticket:** [MVP-045](../docs/tickets/MVP-045.md) · P0 · "M". Branch `feature/mvp-045-catalog-crud` (off main). First catalog ticket; **unblocks MVP-042** (index gen needs catalog_items).

**Files (new):** `migrations/versions/d2cecc53f63c_catalog_items.py` (012), `core/catalog/crud.py`, `core/catalog/router.py`, `tests/integration/test_catalog_crud.py`. **Files (modified):** `core/api/main.py` (mount catalog router).

**Migration 012** (revises `5dcbda42efca`): `CREATE EXTENSION vector`; `catalog_items` (org-scoped +RLS; jsonb attributes, `attributes_schema_ver`, `search_text` tsvector, `embedding vector(1024)`, price_mode/availability/status checks; indexes `(org_id,pack_id,status)`, GIN(search_text), **HNSW**(embedding)); `catalog_items_history` (+RLS; `LIKE … INCLUDING DEFAULTS` + history_id/operation/changed_by/reason/changed_at); `catalog_idempotency` (+RLS). Round-trip verified; roles re-applied.

**crud.py** — `create_item` (Idempotency-Key replay → same item; pack + attributes_schema_ver from the active install; identity-key dedup → `DuplicateIdentity(existing_id)`; history 'insert'), `get_item`, `list_items` (keyset cursor on (created_at,id) desc — stable under concurrent inserts), `update_item` (If-Match on updated_at → `PreconditionFailed`; history 'update'), `delete_item` (soft-delete status='archived'; history 'delete'). Deep attribute validation (JSON Schema + CEL) is MVP-046.
**API** — `POST /v1/catalog/items` (catalog:write; Idempotency-Key → 201/200; identity clash → **409** {existing_id}; NoPack → 422), `GET /v1/catalog/items` (cursor), `GET/PATCH/DELETE /v1/catalog/items/{id}` (If-Match → 412, soft-delete → 204). ETag = updated_at.

**Requirement → evidence (all live, pg):**
| Criterion | Test | Result |
|---|---|---|
| mutation → history row with actor | `test_catalog_crud::test_create_get_and_history`, `::test_update_if_match_and_history`, `::test_soft_delete_archives_and_delists` | PASS |
| identity-key duplicate → error with existing id | `::test_identity_duplicate_raises_with_existing_id` | PASS |
| Idempotency-Key replay → same item, no dupe | `::test_idempotency_key_replays_same_item` | PASS |
| cursor pagination walks all, stable | `::test_cursor_pagination_walks_all` | PASS |
| If-Match optimistic concurrency | `::test_update_if_match_and_history` | PASS |
| no pack installed → refused | `::test_create_without_pack_raises` | PASS |

**Commands:** ruff (pass) · mypy core (67) + migrations (3) (pass) · guards (5, pass) · `pytest -q` **307 passed, 0 skipped** · migration round-trip OK · routes `/v1/catalog/items(/{id})` registered.

**Deferred:** attribute validation JSON Schema + CEL (MVP-046); search/embeddings (047/048); availability + stale-inputs (049). Identity dedup is app-level (a partial unique index could harden the race later — DECISIONS 2026-07-31).

## 2026-07-31 — MVP-046 · Attribute validation (JSON Schema 2020-12 + CEL constraints)

**Ticket:** [MVP-046](../docs/tickets/MVP-046.md) · P0 · "M". Branch `feature/mvp-046-attr-validation` (off main).

**Files (new):** `core/catalog/validate.py`, `tests/unit/test_catalog_validate.py`. **Files (modified):** `core/catalog/crud.py` (validate on create/update; `_active_pack` returns json_schema; `_item_schema` helper), `core/catalog/router.py` (ValidationProblems → 422), `pyproject.toml` (+jsonschema explicit + mypy overrides), `tests/integration/test_catalog_crud.py` (real schema in fixture + a wiring test). No migration, no DB change.

**validate.py** — `validate_attributes(attributes, json_schema, cache_key)` → `[AttributeProblem(path,error,rule)]`: Draft 2020-12 via `jsonschema` with `additionalProperties:false` injected (unknown attrs rejected); if the shape is clean, each `constraints[].cel` is evaluated over `{attributes}` via celpy (failure → `{path:"$", error:message, rule:cel}`). Compiled validators + CEL programs cached per (pack, version). `assert_valid` raises `ValidationProblems`, which the CRUD create/update endpoints map to **422** carrying the path-precise errors.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| jewelry net>gross → exact message | `test_catalog_validate::test_net_greater_than_gross_exact_message` | PASS |
| gold purity must be karat (CEL) | `::test_gold_must_be_karat_constraint` | PASS |
| unknown attribute rejected (additionalProperties) | `::test_unknown_attribute_rejected` | PASS |
| missing required → path-precise | `::test_missing_required_is_path_precise` | PASS |
| enum violation reported | `::test_enum_violation_reported` | PASS |
| valid item → no problems | `::test_valid_item_has_no_problems` | PASS |
| CRUD rejects invalid attributes (wiring → ValidationProblems) | `test_catalog_crud::test_create_rejects_invalid_attributes` | PASS |

**Commands:** ruff (pass) · mypy core (68) (pass) · guards (5, pass) · `pytest -q` **315 passed, 0 skipped**.

**Note:** mid-ticket the `docs/` vault symlink was found replaced by a stray directory (broke 3 event-type tests + doc access); restored with founder approval — moved the stray dir out of the repo, `git checkout -- docs` (BLOCKERS #15, resolved). **Deferred:** search/embeddings (047/048); the restaurant/kirana-specific validation fixtures (only jewelry + kirana packs exist).

## 2026-07-31 — MVP-047 · Text search (BM25)

**Ticket:** [MVP-047](../docs/tickets/MVP-047.md) · P0 · "S". Branch `feature/mvp-047-text-search` (off main).

**Files (new):** `core/catalog/search.py`, `tests/integration/test_catalog_search.py`. **Files (modified):** `core/catalog/crud.py` (search.refresh on create/update; `_active_pack`/`_item_schema` return search_projection), `core/catalog/router.py` (`GET /catalog/search`). No migration (GIN index shipped in 012).

**search.py** — `build_text` composes title + description + projected (`x-search`) attribute values (lists flattened); `refresh` rebuilds `search_text = to_tsvector('simple',t) || to_tsvector('english',t)` after each write (simple keeps exact tokens like "22k" + vernacular aliases; english adds stemming); `search_items` matches `websearch_to_tsquery('simple',q) || ('english',q)` and ranks by `ts_rank` over the GIN index, RLS-scoped. `GET /v1/catalog/search?q=&k=` → `{results, nearest:[]}` (nearest is MVP-048).

**Requirement → evidence (all live, pg):**
| Criterion | Test | Result |
|---|---|---|
| '22k chain' matches only the item with both tokens | `test_catalog_search::test_query_matches_and_exact_token` | PASS |
| alias recall via a projected attribute ('aata') | `::test_projected_alias_recall` | PASS |
| search_text updates on title edit | `::test_search_text_refreshes_on_title_edit` | PASS |

**Commands:** ruff (pass) · mypy core (69) (pass) · guards (5, pass) · `pytest -q` **318 passed, 0 skipped** · route `/v1/catalog/search` registered.

**Deferred:** hybrid embeddings + RRF fusion + nearest-on-empty (MVP-048); attribute filter pushdown (MVP-048); per-locale stemmers beyond en (post-MVP).

## 2026-07-31 — MVP-048 · Embeddings + hybrid RRF (gated-simulated)

**Ticket:** [MVP-048](../docs/tickets/MVP-048.md) · P1 · "M". Branch `feature/mvp-048-embeddings-rrf` (off main). Founder chose to build gated-simulated (2026-07-31).

**Files (new):** `core/catalog/embed.py`, `tests/integration/test_catalog_embed.py`. **Files (modified):** `core/catalog/search.py` (rrf_fuse, kNN, hybrid_search, filter pushdown), `core/catalog/router.py` (`/catalog/search` → hybrid + filters + nearest), `core/common/config.py` (`embeddings_provider_enabled`). No migration (the `embedding vector(1024)` column + HNSW index shipped in 012).

**embed.py** — `Embedder` Protocol; `SimulatedEmbedder` (deterministic seeded-PRNG 1024-dim unit vector — no paid API); `default_embedder` returns it unless `embeddings_provider_enabled` (then the real provider, not wired → NotImplementedError, fail-closed); `to_pgvector`; `embed_pending` (embeds NULL-vector items in the current org context); `run_embeddings_batch` (iterates orgs, each in its own tenant context) + `register_jobs` (scheduler `*/5`). **search.py** — `rrf_fuse` (RRF k=60, deterministic); `_knn` (pgvector `<=>` cosine over HNSW, filterable); `hybrid_search` (BM25 ⊕ kNN with filter pushdown; a neighbour joins `results` only within `SEMANTIC_MAX_DISTANCE`=0.35, else it's `nearest`; empty results → 3 nearest). `GET /v1/catalog/search` returns `{results, nearest}` + `filters=key:value,...`.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| RRF fusion determinism + agreement | `test_catalog_embed::test_rrf_fuse_is_deterministic_and_rewards_agreement` | PASS |
| batch embeds pending items (idempotent) | `::test_embed_pending_fills_vectors` | PASS |
| hybrid returns BM25 matches | `::test_hybrid_returns_bm25_matches` | PASS |
| empty results carry 3 nearest (gr-01 shape) | `::test_hybrid_empty_results_carry_three_nearest` | PASS |
| hybrid deterministic | `::test_hybrid_is_deterministic` | PASS |

**Commands:** ruff (pass) · mypy core (70) (pass) · guards (5, pass — a docstring false-matched session-set-ban; reworded) · `pytest -q` **323 passed, 0 skipped**.

**Deferred (BLOCKERS #16):** real hosted embedding provider (founder picks; §9 dep + creds) behind the flag; scheduler entrypoint wiring (`register_jobs()` + the MVP-028 loop — entrypoint is still a placeholder); cross-encoder rerank + per-locale embedding models (post-MVP). The simulated embedder is not semantic — it validates pipeline mechanics only.

## 2026-08-01 — MVP-042 · Catalog schema registration + index generation

**Ticket:** [MVP-042](../docs/tickets/MVP-042.md) · P0 · "M". Branch `feature/mvp-042-index-gen` (off main). Unblocked by MVP-045 (catalog_items).

**Files (new):** `core/packs/indexes.py`, `migrations/versions/1b9dc38df16c_catalog_generated_ddl.py`, `tests/integration/test_catalog_indexes.py`. **Files (modified):** `core/packs/installer.py` (store generated_ddl at registration), `pyproject.toml` (asyncpg mypy override).

**Migration `1b9dc38df16c`** adds `catalog_schemas.generated_ddl text[]` (additive). **indexes.py** — `generate_index_ddl(pack_slug, pack_id, json_schema)` emits a partial expression index per `x-index` attribute (sorted, deterministic): scalar → btree on `(attributes->>'f')`, `x-index-type:numeric` → `(((attributes->>'f')::numeric))` (triple-paren cast form), array → GIN on `(attributes->'f')`; all partial on `pack_id` + `attributes ? 'f'`. The installer stores these at schema registration. `apply_generated_indexes` runs them CONCURRENTLY over an autocommit **migrator** connection (app_rw has no DDL rights) with `lock_timeout=3s`; a contended `IF NOT EXISTS` statement is deferred and retried next (off-peak) run.

**Requirement → evidence:** jewelry DDL snapshot (6 indexes, verbatim) — `test_catalog_indexes::test_jewelry_ddl_snapshot` PASS; only x-index fields generate — `::test_only_x_index_fields_generate` PASS; installer stores generated_ddl — `::test_installer_stores_generated_ddl` PASS; apply creates indexes idempotently — `::test_apply_creates_indexes_idempotently` PASS.

**Commands:** ruff · mypy core (71) · guards (5) · `pytest -q` **328 passed, 0 skipped** · migration round-trip OK.

**Note:** the ticket's "three expected indexes" predates the jewelry schema gaining weight/gender/occasion x-index attributes; it now declares 6, and the snapshot is the verbatim source of truth. Index-drop-on-schema-upgrade + real contention retry scheduling are out of scope (per ticket).

## 2026-08-01 — MVP-059 · Composer + tenant layer generator

**Ticket:** [MVP-059](../docs/tickets/MVP-059.md) · P0 · "M". Branch `feature/mvp-059-composer` (off main). Deps MVP-058 (registry) + MVP-021 (settings), both done. No migration (uses prompt_layers/bindings from 010).

**Files (new):** `core/prompts/composer.py`, `core/prompts/tenant_layer.py`, `prompts/base/concierge.md`, `tests/integration/test_prompt_composer.py`.

**composer.py** — `render(session, org_id, binding_id, params)` per docs/21-platform/prompt-registry.md: loads the binding's base/vertical/tenant layer ids, loads each layer (immutable per version → id cache, infinite TTL; `clear_cache` for tests), `check_compat` on `requires{}`, composes `base + vertical + render_template(tenant, params)`, returns `ComposedPrompt(text, layer_versions, content_hash=sha256)`. **Fail-closed:** a missing binding/layer → `LayerMissing`, a missing `{param}` → `MissingParam` (the run refuses to start — no silent blanks, no partial prompts). **tenant_layer.py** — `generate_tenant_layer` resolves tenant facts (persona/store/policies/language via settings, with defaults), bakes them into template v1, versions by content hash (identical facts dedupe; changed facts → new version); `resolve_tenant_facts`. `prompts/base/concierge.md` authors base.concierge@1.0 (industry-agnostic safety/tier/tool rules).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| missing param → run refuses to start | `test_prompt_composer::test_render_template_fills_and_is_strict`, `::test_missing_param_refuses` | PASS |
| hash reproducible across processes | `::test_hash_reproducible_across_cache_clear` | PASS |
| compose stacks base+vertical+tenant, versions stamped | `::test_compose_stacks_layers_and_hashes` | PASS |
| missing binding/layer fails closed | `::test_unknown_binding_fails_closed` | PASS |
| tenant layer baked from settings, idempotent | `::test_generate_tenant_layer_is_idempotent_and_bakes`, `::test_resolve_tenant_facts_defaults` | PASS |

**Commands:** ruff · mypy core (73) · guards (5) · `pytest -q` **336 passed, 0 skipped**.

**Deferred:** the settings-change → regeneration hook wiring (enqueue) + the composed smoke-suite gate (needs the eval harness, MVP-095/096); base layers for the other archetypes (only concierge authored here, per ticket).

## 2026-08-02 — MVP-050 · Pricing migration + rules_v1 engine (the money engine)

**Ticket:** [MVP-050](../docs/tickets/MVP-050.md) · P0 · "L". Branch `feature/mvp-050-pricing-engine` (off main). The money invariant: all committable figures computed deterministically, exactly, with provenance.

**Files (new):** `migrations/versions/63bcec3ea528_pricing.py` (013), `core/pricing/functions.py`, `core/pricing/engine.py`, `core/pricing/registry.py`, `tests/unit/test_pricing_engine.py`, `tests/integration/test_pricing_registry.py`.

**Migration 013** (revises `1b9dc38df16c`): `pricing_strategies`/`rate_sources`/`rate_snapshots` (global), `pricing_rules`/`quotes`/`committed_figures_ledger` (+RLS). `quotes.computed_by` CHECK = 'engine' (never an LLM); `quotes.stale_inputs` for MVP-049. Round-trip verified; roles re-applied.

**engine.py** — `compute(strategy_rules, inputs, params, *, rate_lookup, tax_rules, item_lookup, source_for, offer_discount)` runs the ordered stages and returns `Quote(breakdown, total_minor, rate_snapshot_ids)`. Formulas are a CEL-ish DSL evaluated by a **safe AST interpreter** (`_ALLOWED_NODES` whitelist — no `eval`/imports/attribute abuse) after preprocessing (`&&`/`||`/`!`, `x[].f` projection, `map(seq,v,e)`, `c ? a : b`). **Exact:** int minor / `Decimal` only, floats rejected; each money stage must be an integer minor value (residue → `unledgered_figure`); `rate()` pins snapshots into provenance; `stale_rate` fails closed. **functions.py** — Decimal `round`(modes)/`sum`/`min`/`max`, `TaxRule.apply`, `DotItem` (attr access), `to_decimal` (float-reject). **registry.py** — `load_strategy`/`get_strategy` + `build_source_for`/`build_tax_rules` derived from the strategy yaml.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| jewelry goldens (pg-001 full breakdown, pg-002 making-floor, pg-031 silver, stones projection) | `test_pricing_engine::test_pg001_*`, `_pg002_*`, `_pg031_*`, `_stones_projection_summed` | PASS |
| **kirana goldens on the SAME engine, zero changes** (subtotal, delivery ternary, out-of-radius guard) | `::test_kpg01_*`, `_kpg02_*`, `_kpg04_*` | PASS |
| exact minor units, no floats, per-stage residue, rounding modes | `::test_float_input_rejected`, `_residue_stage_fails_closed`, `_rounding_modes` | PASS |
| stale rate fails closed; disallowed syntax rejected | `::test_stale_rate_fails_closed`, `_disallowed_syntax_rejected` | PASS |
| strategy registry load/get + compute from DB rules | `test_pricing_registry::test_load_and_compute_from_registry` | PASS |

**Commands:** ruff · mypy core (76) · guards (5) · `pytest -q` **351 passed, 0 skipped** · migration round-trip OK.

**Flagged (DECISIONS 2026-08-02):** the sample golden **pg-014** expects a discount of 239600 (5% of metal only) but the authoritative strategy.yaml formula caps at 5% of the full subtotal (incl. making) → 258768; the engine follows the formula, and if "exclude making" is the intended rule the *pack formula* should change, not the engine. The repo golden files are illustrative samples, not the full 200/60 suites.

**Deferred:** WASM strategy execution (do-not-build fence); rate ingestion (MVP-051, gated); quotes API + replay (052); ledger writes (053); item()/offer_discount() wiring to real catalog/offers (052).

---

## 2026-08-02 — MVP-052 + MVP-053 · Quote service/API + committed-figures ledger

**Tickets:** [MVP-052](../docs/tickets/MVP-052.md) (quote compute/replay + API) · [MVP-053](../docs/tickets/MVP-053.md) (committed-figures ledger). Branch `feature/mvp-052-quotes-api` (off main). Built together — 053's ledger is written *by* 052's compute and read by the send gate (054). **No migration** (migration 013 already created `quotes` + `committed_figures_ledger`).

**Files (new):** `core/pricing/service.py`, `core/pricing/api.py`, `core/pricing/ledger.py`, `tests/integration/test_pricing_service.py`. **(modified):** `core/pricing/registry.py` (`get_strategy` now returns the full strategy dict so engine lookups rebuild at compute time; `load_strategy` stores the whole strategy in `rules`), `core/api/main.py` (wire `pricing_router` + `rates_router`), `tests/integration/test_pricing_registry.py` (follow the `get_strategy` shape change).

**service.py** — `compute_quote(session, org_id, *, strategy_key, inputs, params, lead_id?, conversation_id?, valid_hours)` resolves the strategy, **pre-loads the pack's freshest in-window snapshot per source** into a dict so the synchronous engine gets a synchronous `rate_lookup` (no async call inside `compute`), runs `engine.compute`, then writes the `quotes` row (inputs+params, breakdown, pinned `rate_snapshot_ids`, total, `valid_until`) **and** `ledger.write(figures_from_breakdown(...))` in **one transaction** — the caller commits, so a ledger failure rolls back the quote too. `replay_quote(session, org_id, quote_id)` reloads the stored inputs/params + strategy, **pins the exact snapshots the quote used** (`_pinned_rate_lookup`), recomputes, and returns a byte-for-byte `matches`. `rates_status` reports per-source freshness.

**ledger.py (MVP-053)** — `Figure(figure_type, amount_minor?, value_text?)`; `write` records the quote total + every **positive** breakdown line with an expiry; `match(org, amount_minor, window_hours=48)` is **exact (tolerance 0)**, unexpired, within the window — an off-by-one or expired figure fails closed (this is the input to the MVP-054 send gate). `figures_from_breakdown` excludes zero lines and the `total` breakdown id (added once as `figure_type='total'`).

**api.py** — `POST /v1/pricing/compute` → 200 `{quote_id}` / **409 `stale_rate`** / 422 other `PricingError`; `POST /v1/pricing/replay` → `{matches, stored_total, recomputed_total}`; `GET /v1/rates/status`. All `requires(CATALOG_READ)` (enforcement itself already covered by `test_rbac`). The agent `pricing.compute` tool will call the service in-process, not over HTTP.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| quote persists with pinned rate provenance | `test_pricing_service::test_compute_writes_quote_with_provenance` | PASS |
| ledger written; **every** breakdown-visible amount matchable; off-by-one fails closed | `::test_ledger_written_and_every_figure_matchable` | PASS |
| **replay is byte-exact** from the pinned snapshots | `::test_replay_is_byte_exact` | PASS |
| compute + ledger are **atomic** (ledger failure ⇒ zero quotes) | `::test_compute_is_atomic_when_ledger_fails` | PASS |
| **stale rate fails closed** (409-mapped) | `::test_stale_rate_fails_closed` | PASS |
| expired ledger row no longer matches | `::test_expired_ledger_row_no_longer_matches` | PASS |
| registry round-trip still computes a golden after the shape change | `test_pricing_registry::test_load_and_compute_from_registry` | PASS |

**Commands:** ruff (core+tests) · mypy core (**79 files**) · guards (`test_scaffold`, 2) · `pytest -q` **357 passed, 0 skipped** (+6). App builds; OpenAPI shows `/v1/pricing/compute|replay`, `/v1/rates/status`.

**Commit:** feature `e4b639e` → merge `d57eab5` (pushed to `origin/main`).

**Security:** RLS scopes ledger + quotes to the caller's org (`set_org_context`); `computed_by='engine'` CHECK keeps an LLM off the figure; no secrets/PII in figures (amounts + figure-type labels only); external side effects unaffected.

**Deferred:** MVP-049 (`stale_inputs` on rule-referenced attribute change); MVP-054 (send-path extractor → `ledger.match` → 422 `unledgered_figure`); item()/offer_discount() wiring for kirana line-item strategies (jewelry pilot is rate-based, needs neither); params today come from the request/caller — settings-slot resolution is the production path.

---

## 2026-08-02 — MVP-054 · Send-path figure check (the last line of defence)

**Ticket:** [MVP-054](../docs/tickets/MVP-054.md) · P0 · "M". Branch `feature/mvp-054-send-figure-check` (off main). *"No unledgered rupee amount leaves the building."* Completes the enforcement triangle: the engine computes figures (050), quotes write them to the ledger atomically (052/053), and this gate blocks any outbound figure that isn't there.

**Files (new):** `core/pricing/extract.py`, `tests/unit/test_money_extract.py`, `tests/integration/test_send_figure_check.py`. **(modified):** `core/channels/whatsapp/send.py` (Gate 5 + `figure_check`/`figure_override_by` params + `_assert_figures_ledgered`). **No migration** (reads the 053 ledger).

**extract.py** — pure Python, no I/O, no model (IDL-008). `extract_amounts(text) -> list[Figure(minor, raw)]` parses ₹/Rs/Rs./INR/rupee(s), lakh/lac/crore/cr/k/thousand magnitude words, Indian lakh grouping (`1,00,000`) and paise, via `Decimal` (never a float; `float-money` guard stays green). **Conservative** to hold the <0.5% false-positive AC: a digit run is money only with a currency marker, a magnitude word, or unmistakable Indian grouping (a 2-digit group between commas) — so a phone number, an order id, or `8 pm` is not a figure. Western `12,345` without currency stays ambiguous (not extracted).

**send.py Gate 5** — runs after suppression+consent, before the queued row and any Meta call. Every amount in `body` must `ledger.match` (exact, unexpired) or:
- `block` (default, fail-closed): raise `SendRefused("unledgered_figure")` → **422** (canonical taxonomy), Meta never called;
- tier-3 `figure_override_by`: proceed, but append `msg.send.figure_override` to the org's audit chain (payload = unledgered **count** only — amounts are never logged or audited, §10.2);
- `warn` (W2): proceed, redacted breadcrumb (count only);
- `off`: skip (controlled rollout only). Warn→block flips via the `ledger_check.block` flag at the call site.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| mt-* Indian money formats (₹/Rs/INR, lakh, crore, k, trailing 'rupees', paise) parsed to exact minor | `test_money_extract::test_extracts_indian_money_formats` (8 cases) | PASS |
| bare numbers (phone / time / id / quantity) are **not** figures; Western grouping not assumed money | `::test_ignores_bare_numbers`, `_western_grouping_without_currency_is_not_assumed_money` | PASS |
| Indian grouping alone is money; multiple figures; Decimal-exact paise | `::test_indian_grouping_alone_*`, `_multiple_figures_*`, `_paise_rounding_is_decimal_exact` | PASS |
| ledgered figure sends; **unledgered blocks with `unledgered_figure`, no Meta call** | `test_send_figure_check::test_ledgered_figure_is_allowed`, `_unledgered_figure_blocks_and_never_hits_the_wire` | PASS |
| partial (one ledgered + one not) still blocks | `::test_partial_match_still_blocks` | PASS |
| warn mode allows; tier-3 override proceeds **and is audited** | `::test_warn_mode_allows_*`, `_tier3_override_proceeds_and_is_audited` | PASS |

**Commands:** ruff (core+tests) · mypy core (**80 files**) · guards (5: core-not-verticals, industry-nouns, **float-money**, **send-call-sites**, session-set-ban) · `pytest -q` **378 passed, 0 skipped** (+21). Existing MVP-034 gates + send tests unaffected (their bodies carry no figures).

**Commit:** merge `fea254a` (pushed to `origin/main`).

**Security:** the gate is the send-path invariant — an AI-drafted price that was never computed cannot leave. Amounts stay out of logs/audit (count only); override is attributable on the hash chain; no external side effect added (Meta stays gated-simulated).

**Deferred:** the 30-day staging false-positive replay (AC — needs a staging corpus, BLOCKERS); block-explanation UI in takeover mode (frontend, later); `figure_refs` explicit-declaration path (accepted, unused — the real check is on `body`).

---

## 2026-08-02 — MVP-049 · Availability + price-input staleness

**Ticket:** [MVP-049](../docs/tickets/MVP-049.md) · P1 · "S". Branch `feature/mvp-049-availability-stale` (off main). *"Quotes must know when their inputs changed under them."* **No migration** (`quotes.stale_inputs` was created in 013).

**Files (new):** `core/catalog/availability.py`, `tests/unit/test_availability.py`, `tests/integration/test_availability_stale.py`. **(modified):** `core/catalog/crud.py` (`update_item` now diffs old vs new attributes → `flag_quotes_if_price_inputs_changed`).

**availability.py** (Rule-Zero clean — no industry noun):
- **Transitions** — a constant graph over `catalog_items.availability` (`in_stock` ↔ `made_to_order` ↔ `out`; `bookable_slot` is clinic/out-of-MVP, so never a source or target). `transition(session, org, item, to_state, *, actor_id, actor_type, reason)` validates, updates, and appends `catalog.availability_changed` to the org's audit hash-chain — an agent-actor change is attributable.
- **Price inputs** — `price_input_deps(strategy)` walks each stage's **rule AST** (`ast.parse(to_python(formula))`, reusing the engine's preprocessing) and collects `inputs.<field>` references; nothing is hard-coded (jewelry → `{net_weight_g, purity, stones, requested_discount_minor}`; the same walk yields kirana's own inputs). `flag_stale_quotes_for_item` flags open (draft, unexpired) quotes whose stored `inputs.item_id` matches; `flag_quotes_if_price_inputs_changed` only fires when a changed attribute is in the strategy deps.

**Linkage decision (disclosed):** the ER diagram E3 has **no** quote→item FK, but the catalog-abstraction spec says "open quotes **referencing the item**" are flagged. A quote references an item when its stored `inputs.item_id` equals the item — the natural, no-migration linkage. A rate-only quote (no `item_id`) is correctly unaffected by catalog edits (its staleness is rate staleness, handled by `stale_rate`).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| rule-AST extractor per jewelry stage (metal_value→weight+purity, stones→stones, discount→discount, making/gst/total→none) | `test_availability::test_jewelry_extractor_per_stage`, `_price_input_deps_*` | PASS |
| extractor is pack-agnostic (kirana inputs, no jewelry knowledge) | `::test_extractor_is_pack_agnostic` | PASS |
| transition graph closed; bookable_slot excluded | `::test_transition_graph_is_closed_over_known_states` | PASS |
| **agent transition writes an audit entry** + updates state | `test_availability_stale::test_agent_transition_updates_and_is_audited` | PASS |
| invalid transition raises | `::test_invalid_transition_raises` | PASS |
| only open, referencing quotes flagged (not other-item / sent / expired) | `::test_flag_only_open_referencing_quotes` | PASS |
| **weight edit flags the dependent open quote; unrelated (gender) edit does not** | `::test_weight_edit_flags_dependent_quote_unrelated_edit_does_not` | PASS |

**Commands:** ruff (core+tests) · mypy core (**81 files**) · guards (5, incl. **industry-nouns** — availability.py clean) · `pytest -q` **386 passed, 0 skipped** (+8).

**Commit:** merge `33ac11e` (pushed to `origin/main`).

**Deferred (BLOCKERS #17):** the typed `catalog.price_inputs_changed` event — its payload schema must be registered in the vault's read-only `topics.yaml` (§4); `emit()` rejects unregistered types and the drift test enforces it. The MVP-visible `stale_inputs` flag is written synchronously and tested; the async fan-out (concierge auto-recompute) lands once the event is registered + the agent runtime exists. Rollout note in the ticket confirms "stale_inputs starts as a dashboard-visible flag only."

---

## 2026-08-02 — MVP-051 · Rate ingestion + manual entry

**Ticket:** [MVP-051](../docs/tickets/MVP-051.md) · P0 · "M". Branch `feature/mvp-051-rate-ingestion` (off main). *"Fresh IBJA rates or a fail-closed refusal — never a guess."* **No migration** (rate_sources/rate_snapshots exist since 013).

**Files (new):** `core/pricing/rates.py`, `tests/unit/test_rate_bounds.py`, `tests/integration/test_rate_ingestion.py`. **(modified):** `core/pricing/api.py` (`POST /v1/rates/manual`), `core/common/config.py` (`rates_provider_enabled` gate).

**rates.py** — `ingest_rate(source_key, value, apply_bounds=True)` writes a `rate_snapshots` row unless any shared key jumps more than the source's `fetch_spec.bounds.max_step_pct` vs the last good value → **quarantined** (no write, so the last good snapshot stays newest and the staleness clock is untouched). `fetch_and_store` fetches (simulated by default), ingests, and — via a global raw-stream publish (`gop:events:<type>`, the DLQ-alert precedent, so no org context is needed) — raises **`alert.ops.v1`** on quarantine and `rate.updated.v1` on success. `record_manual_rate` writes an owner-entered rate (no bounds — a human is authoritative) and appends `rate.manual_entry` to the org's audit chain with **keys only** (never the rate values). **Gated-simulated:** `SimulatedRateFetcher` (deterministic, no network) is the default; `HttpRateFetcher` raises `provider_unavailable` until `rates_provider_enabled` + a chosen IBJA endpoint (BLOCKERS #5).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| staleness boundary — ≤24h fresh, >24h → `stale_rate` (the compute 409) | `test_rate_ingestion::test_staleness_boundary_fresh_then_stale` | PASS |
| +12% step quarantined, **no snapshot written** (clock unaffected) + `alert.ops` emitted | `::test_out_of_bounds_quarantined_no_snapshot_and_alert` | PASS |
| bounds math: within/over/at-boundary/new-key | `test_rate_bounds::test_within_bounds_ok`, `_step_over_bound_*`, `_exactly_at_bound_*`, `_new_key_*` | PASS |
| successful fetch writes a snapshot + publishes `rate.updated` | `test_rate_ingestion::test_fetch_writes_snapshot_and_publishes_updated` | PASS |
| manual entry: written, audited, **values not in the audit payload** | `::test_manual_entry_writes_snapshot_and_audits` | PASS |
| real provider fails closed when disabled | `test_rate_bounds::test_http_fetcher_fails_closed_when_disabled` | PASS |

**Commands:** ruff (core+tests) · mypy core (**82 files**) · guards (5) · `pytest -q` **396 passed, 0 skipped** (+10). `POST /v1/rates/manual` + `GET /v1/rates/status` in the OpenAPI.

**Commit:** merge `7d95a7a` (pushed to `origin/main`).

**Security / external effects:** no real network call — the IBJA HTTP source is gated and fails closed (#5); manual entry is owner-permissioned + audited (values redacted from audit); quarantine + alert give a human the final say on an implausible rate.

**Deferred:** real **tier-2 approval** on manual entry (approvals engine is MVP-065; today an owner permission + audit stands in); **scheduler firing** of `fetch_and_store` (the scheduler entrypoint is still the MVP-028 placeholder, cf. #16 — the job function is built and tested, just not yet scheduled); the **org fan-out** of `rate.updated`/`rate.stale` (published globally to the stream; per-org routing awaits the runtime).

---

## 2026-08-02 — MVP-055 · Executor skeleton + checkpoints (the agent runtime core)

**Ticket:** [MVP-055](../docs/tickets/MVP-055.md) · P0 · "L". Branch `feature/mvp-055-executor` (off main). *"Conversation state survives any crash and resumes without duplicate effects."* **Founder-approved (2026-08-02):** adopt LangGraph + land the runtime migration ahead of approvals-014 (DECISIONS).

**Files (new):** `core/runtime/model.py`, `core/runtime/graph.py`, `core/runtime/executor.py`, `core/runtime/ops_router.py`, `migrations/versions/f124e1102952_runtime.py`, `tests/unit/test_runtime_graph.py`, `tests/integration/test_executor.py`. **(modified):** `core/api/main.py` (ops router), `core/common/config.py` (`llm_provider_enabled`), `pyproject.toml`/`uv.lock` (`langgraph>=0.2,<0.3`), `project-management/DECISIONS.md`.

**Dependency (§9):** `langgraph==0.2.76` (MIT) + 17 transitive (langchain-core 0.3.86, langgraph-checkpoint 2.1.2, langsmith, orjson, tenacity, …) — the footprint was disclosed and approved. LangGraph **sequences**; the platform gates stay the authority.

**Migration 015** (`f124e1102952`, revises `63bcec3ea528`): `model_routes` (global), `agent_runs`/`agent_steps`/`agent_memory` (+RLS). `agent_runs` carries `composed_prompt_hash` + `permission_manifest_hash` **NOT NULL** and a `cursor`; `agent_steps` has `state` jsonb + `UNIQUE(run_id, seq)` (idempotent re-checkpoint). Upgrade/downgrade round-tripped; `make db-roles` re-applied grants. Lands ahead of approvals-014 — no FK crosses (founder-approved ordering).

**Design:** `graph.py` declares the LangGraph `StateGraph` route→compose→model_turn→(tool_call↔)respond with a bounded tool loop; the **same** node fns + `model_turn` branch drive `executor.py`, so the declared graph and the durable driver can't diverge. The executor runs one node at a time and writes a **durable checkpoint after every node** (Redis snapshot + `agent_steps` row); before each node it enforces the **kill switch** (feature flag, fail-closed), **budget** (instance `max_steps`), and a per-node **timeout**. Crash model: an in-flight (uncheckpointed) node is re-run on resume — route/compose/model_turn/tool_call are pure, and `respond`'s external effect is **idempotent on the run id** (the real send-path contract), so a replay never double-sends. `model.py` is a gated-simulated, provider-agnostic model (`RealModel` fails closed on `llm_provider_enabled`).

**Checkpointing note (disclosed):** a fully-correct LangGraph durable `BaseCheckpointSaver` (600-line surface, version-fragile) was **not** implemented; instead the executor owns durable Redis+Postgres checkpointing + resume. LangGraph remains the declared orchestration engine (graph + edges + conditional routing) and runs natively in tests.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| **chaos-kill 10/10 resume, no duplicate send** (crash at model_turn/tool_call/respond/after-effect) | `test_executor::test_chaos_kill_resume_no_duplicate_send` | PASS |
| `composed_prompt_hash` + `permission_manifest_hash` on every run | `::test_happy_path_records_both_hashes_and_sends_once` | PASS |
| checkpoint-conflict retry (re-insert `(run_id,seq)` is a no-op) | `::test_checkpoint_reinsert_is_idempotent` | PASS |
| kill switch + budget interrupt fail-closed (no send) | `::test_kill_switch_interrupts_before_sending`, `_budget_cap_interrupts` | PASS |
| run is tenant-isolated (RLS) | `::test_run_is_tenant_isolated` | PASS |
| LangGraph graph runs route→respond; bounded tool loop; deterministic hash; provider gated | `test_runtime_graph::*` (6) | PASS |

**Commands:** `uv add langgraph` (resolved 0.2.76) · ruff (core+tests) · mypy core (**86 files**) · guards (5, incl. industry-nouns — runtime clean) · alembic upgrade/downgrade/upgrade round-trip + `make db-roles` · `pytest -q` **408 passed, 0 skipped** (+12). `GET /v1/ops/runs/{id}` in the OpenAPI.

**Commit:** merge `4824fb6` (pushed to `origin/main`).

**Security:** new runtime tables are RLS-scoped + cross-tenant tested; AI output stays untrusted (model only proposes a tool/text — figures never invented; customer text still faces the MVP-054 send gate); no paid API (simulated model); ops viewer is `PLATFORM_ADMIN` only.

**Deferred:** real LLM provider (go-live, provider-agnostic); **mediation / permission proxy** (MVP-060 — the `tool_call` node runs a simulated tool for now); a LangGraph durable saver; wiring runs into the worker/scheduler + the `respond` node into the real MVP-054 send path.

---

## 2026-08-02 — MVP-060 · Mediation proxy (the only model→tool path)

**Ticket:** [MVP-060](../docs/tickets/MVP-060.md) · P0 · "L". Branch `feature/mvp-060-mediation-proxy` (off main). *"As the only path from model to tools I enforce manifests, params, rates, budgets, tiers, and audit — in that order."* **No migration, no new dependency.**

**Files (new):** `core/mediation/proxy.py`, `core/mediation/tools.py`, `tests/integration/test_mediation_proxy.py`. **(modified):** `scripts/guards.py` + `tests/unit/test_lint_guards.py` (the `runtime-not-tools` guard).

**proxy.py** — `call(ctx, tool_name, params, *, session, redis, registry?, tier_eval?)` runs the authoritative chain in order (tool-permission-model.md): **1** manifest integrity (sha256 vs `ctx.manifest_hash`) → **2** grant lookup → **3** untrusted-content narrowing → **4** param constraints (jsonschema on schema-shaped constraints) → **5** rate limit (redis per-min window) → **6** budgets (per-day send cap) → **7** tier (if `requires_tier_eval`, ≥2 ⇒ `ApprovalPending`, run checkpointed) → **8** audit intent (log-then-act, keys-only payload) → **9** execute registry impl → **10** egress scrub. A denial returns a structured **recoverable `ToolError`** the model can adapt to (never the manifest); manifest-scope denials are **audited + `alert.ops`**, bump a per-run violation counter, and **abort the run (`RunAborted`) at ≥3**.

**tools.py** — `REGISTRY`: `catalog.search`→hybrid_search, `pricing.compute`→compute_quote, `ledger.read`→ledger.match are wired; `messages.send` is registered but reached only past a tier-2 approval (never fires unapproved, §19); `calendar.book`/`crm.read`/`crm.write` are gated stubs (`provider_unavailable`) until built.

**runtime-not-tools guard** — the ticket's "import-linter contract runtime↛tool impls", implemented via the existing AST lint-guard pattern (no `import-linter` dependency, §9): `core/runtime/` may import `core.mediation.proxy` but not `core.catalog`/`core.channels`/`core.mediation.tools`/`core.pricing.{service,ledger,rates,api}`.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| **out-of-manifest call denied + audited + alerted** | `test_mediation_proxy::test_out_of_manifest_denied_audited_alerted` | PASS |
| **≥3 manifest violations abort the run** | `::test_three_manifest_violations_abort_run` | PASS |
| check order — integrity / param / rate / budget-before-tier / tier→pending / narrowing | `::test_manifest_integrity_failure_*`, `_param_constraint_violation`, `_rate_limit_*`, `_budget_checked_before_tier`, `_tier_eval_returns_approval_pending`, `_untrusted_narrowing_*` | PASS |
| successful read tool executes + audits intent (log-then-act) | `::test_successful_read_tool_executes_and_audits_intent` | PASS |
| runtime↛tools contract enforced (red on direct import, green on proxy) | `test_lint_guards::test_runtime_not_tools_*` | PASS |

**Commands:** ruff (core+tests+scripts) · mypy core (**88 files**) · guards (**6** now, incl. runtime-not-tools) · `pytest -q` **419 passed, 0 skipped** (+11).

**Commit:** merge `1169694` (pushed to `origin/main`).

**Security:** the proxy is the structural choke point — manifests/params/rates/budgets/tiers/audit cannot be bypassed (guard-enforced); external actions (messages.send) are tier-gated to human approval by default; denials never leak manifest contents; audit is log-then-act with keys-only payloads.

**Out of scope (ticket):** live policy-engine tier decisions (stubbed conservative-2 until MVP-065). **Deferred (disclosed):** ed25519 manifest **signature** verify (hash integrity is checked now); real destination-aware **PII egress** scrub (pass-through hook); scalar policy constraints (recorded, enforced by the policy engine later); **wiring the executor `tool_call` node through `proxy.call`** — the ApprovalPending/RunAborted handling lands with the approvals engine (MVP-065); the guard already enforces the boundary now so nothing can bypass the proxy in the meantime.

---

## 2026-08-02 — MVP-065 · Policy engine (deterministic action tiers)

**Ticket:** [MVP-065](../docs/tickets/MVP-065.md) · P0 · "L". *"Every side effect gets a deterministic tier from declarative rules."* **⚠️ Process deviation (disclosed):** committed **directly to `main`** (`528bcbe`) — the feature branch was never created (a §7.1 violation, same class as the earlier MVP-047 slip). Code is complete + green; pushed history is left as-is (no force-push); branch discipline resumed for subsequent work.

**Files (new):** `core/approvals/engine.py`, `migrations/versions/1993ba538f4f_approvals_policy.py`, `tests/unit/test_approval_determinism.py`, `tests/integration/test_approval_engine.py`. **(modified):** `core/mediation/proxy.py` (tier check now calls the engine by default).

**Migration 014** (`1993ba538f4f`, revises `f124e1102952`): `approval_policies` (core/pack **global** rows with `org_id NULL` + **tenant** rows; a **custom RLS** policy reads globals + own and writes only own — the standard helper would hide the globals), `trust_ledger`, `incident_tightening`, `execution_token_jti` (the 066 token store), all +RLS. Round-tripped; `make db-roles` re-applied. Chains off the runtime migration (015) — no FK crosses (founder-approved ordering).

**engine.py** — `evaluate(session, ctx: ActionContext) -> Decision`: sets org context, loads contributors for the `action_type` — **core tier-4 minimums** (`CORE_TIER4_ACTIONS`, platform code), **pack + tenant** rows from `approval_policies`, and any active **incident-tightening** — filters each rule by its **CEL** expression against the ctx (compiled once per expr, cached in `_PROGRAM_CACHE`), and returns the **max tier** with the matched rule ids. `select_decision` is a **pure, order-independent** max (ties broken by a stable key) → the determinism property. `validate_tenant_rule` computes the core/pack `baseline_tier` and rejects a tenant rule that would lower it (**tighten-only**). No matching rule ⇒ fail-safe `DEFAULT_UNKNOWN_TIER=2`.

**Live wiring** — `core/mediation/proxy.py` step 7 now calls `engine.evaluate` by default (a tool call becomes an `ActionContext`); an injected `tier_eval` still overrides it for hermetic tests. The MVP-060 proxy tests stay green because an org with no rules resolves tier-eval tools to the fail-safe tier 2.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| ap-01 core tier-4 never autonomous | `test_approval_engine::test_ap01_core_tier4_never_autonomous` | PASS |
| ap-02 pack default; ap-03 CEL-guarded tier; ap-04 **max-tier-wins** | `::test_ap02_*`, `_ap03_cel_guarded_tier`, `_ap04_max_tier_wins` | PASS |
| ap-05 **tighten-only** validator (tighten ok, loosen rejected) | `::test_ap05_tighten_only_validator` | PASS |
| ap-13 incident tightening active→expired; ap-15 unknown fail-safe; tenant tightens over pack | `::test_ap13_*`, `_ap15_unknown_action_fails_safe`, `_tenant_rule_tightens_over_pack` | PASS |
| **determinism: 10k shuffled orderings identical** | `test_approval_determinism::test_determinism_over_10k_shuffles` | PASS |
| CEL compile cache reused; **5 ms budget** (p95 measured) | `::test_cel_compile_cache_reuses_program`, `_evaluation_budget_p95` (**0.878 ms**) | PASS |

**Commands:** ruff (core+tests) · mypy core (**89 files**) · guards (6) · alembic upgrade/downgrade/upgrade + `make db-roles` · `pytest -q` **433 passed, 0 skipped** (+14).

**Security:** tenant rules can only tighten (validator); global policy rows are migrator-written (the NOBYPASSRLS app can't forge core/pack rules); RLS scopes tenant rows + trust/incident/jti to the org; the engine is now the authority the mediation proxy consults for every tier-eval action (§19).

**Out of scope (ticket):** `execution_token` minting + the approval-object lifecycle (requested→notified→approved→executed) → **MVP-066** (the jti table is created here for it). **Deferred (disclosed):** the DB rules-**loading** version cache (the **compile** cache is the named deliverable and is in; evaluation p95 is already 0.88 ms — DB load is not the 5 ms target); the exact ap-* fixture suite is in the vault (illustrative — tested the documented semantics); 48h staging shadow-compare against the yaml-stub before flipping the enforcement flag (rollout note).

---

## 2026-08-02 — MVP-065b · Audit hardening (CEL fail-safe + isolation tests)

**Context:** batch-audit follow-up (founder-approved: "fix findings, then MVP-066"). Branch `feature/mvp-065b-hardening` (off main). Two audit findings, both minor/non-blocking, now closed. No migration, no dependency.

**Files (modified):** `core/approvals/engine.py`, `tests/unit/test_approval_determinism.py`. **(new):** `tests/isolation/test_batch_rls.py`.

**Finding 1 — CEL not fail-safe (fixed).** `engine._matches` caught only `CELEvalError`; a policy whose CEL failed to **compile** raised `CELParseError` and crashed `evaluate()`. Worse, the eval-error path returned `False` (rule dropped), which could **loosen** a broken tightening rule. Fix: any compile/eval failure now **fails safe by treating the rule as matching**, so its declared `tier` still contributes — and because the engine takes the max, an unresolved guard can only tighten, never loosen. Not triggerable in the designed system (certified pack rules, templated tenant rules), so defence-in-depth. Test: `test_approval_determinism::test_malformed_cel_fails_safe_to_matching`.

**Finding 2 — isolation-test coverage gap (fixed).** RLS was enabled+forced on `quotes` / `committed_figures_ledger` / `approval_policies` (and manually probed during the audit), but only `agent_runs` had an automated cross-tenant test. Added `tests/isolation/test_batch_rls.py` (probes as the real non-bypass `app_rw` role): `quotes` + `committed_figures_ledger` show own rows only and fail closed without context; `approval_policies` (mixed scope) shows **globals + own tenant rows, never another org's**, and only globals without context.

**Commands:** ruff · mypy core (89) · guards (6) · `pytest -q` **436 passed, 0 skipped** (+3). Batch audit otherwise verified clean (migrations round-trip, RLS forced on all 10 batch tables, `get_db` one-tx atomicity, `quotes.computed_by='engine'` CHECK, no secrets).

---

## 2026-08-03 — MVP-066 · Execution tokens (no token, no side effect)

**Ticket:** [MVP-066](../docs/tickets/MVP-066.md) · P0 · "S". Branch `feature/mvp-066-execution-tokens` (off main). *"Side-effect services execute only decisions the engine actually made."* **No new migration** (`execution_token_jti` created in migration 014); **no new dependency** (ed25519 via `cryptography`, already used for pack signing).

**Files (new):** `core/approvals/tokens.py`, `tests/integration/test_execution_tokens.py`. **(modified):** `core/common/config.py` (`execution_token_signing_seed`), `core/channels/whatsapp/send.py` (Gate 2 stub → real verify), `core/channels/whatsapp/normalizer.py` (mint a real token for the STOP-confirm), `tests/integration/test_whatsapp_send.py` / `test_whatsapp_templates.py` / `test_send_figure_check.py` (mint real per-send tokens).

**tokens.py** — `mint(session, *, org_id, ctx_hash, tier, ttl_s=600)` builds `{jti, ctx_hash, tier, exp}`, signs it with the platform **ed25519** key (seed from config), persists the unused jti in `execution_token_jti`, and returns `base64(body).base64(sig)`. `verify(session, token, *, org_id, expected_ctx_hash)` checks, in order: signature (forged/tampered → `bad signature`), ctx hash (`ctx mismatch` — a token for one action can't authorize another), expiry (`expired`), then **claims the jti atomically** (`UPDATE … WHERE used_at IS NULL AND expires_at > now() RETURNING` → no row ⇒ `replayed or unknown`). `action_hash(org, action, resource)` is the deterministic binding. Twin of the audit-capability gate: both required before a side effect.

**Stub removal (flag day):** `send.py` Gate 2 now calls `tokens.verify(...)` bound to `action_hash(org, "msg.send", conversation_id)` (refuses `approval_required` on any `TokenInvalid`); the `_verify_execution_token` stub and the normalizer's `_AUTO_CONFIRM_TOKEN` constant are **deleted** — the STOP-confirm mints a real token alongside its audit capability in one transaction. `grep` confirms **zero execution-token stub references** in `core/`.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| **token replay rejected** (jti single-use) | `test_execution_tokens::test_replay_is_rejected` | PASS |
| **ctx mismatch rejected** (token bound to one action) | `::test_ctx_mismatch_is_rejected` | PASS |
| **payload swap rejected** (breaks the signature) | `::test_swapped_payload_breaks_signature` | PASS |
| **expiry rejected** | `::test_expired_token_is_rejected` | PASS |
| valid token verifies once; missing/malformed rejected | `::test_valid_token_verifies_once`, `_missing_and_malformed_rejected` | PASS |
| send path enforces a real token (all send/template/figure tests mint tokens) | `test_whatsapp_send` / `_templates` / `_send_figure_check` (21) | PASS |

**Commands:** ruff (core+tests) · mypy core (**90 files**) · guards (6) · `pytest -q` **442 passed, 0 skipped** (+6, plus 21 send tests migrated to real tokens).

**Commit:** merge `8bc4b5a` (pushed to `origin/main`).

**Security:** the execution token is the second required capability at the send exit (with the audit capability); it is signed (unforgeable), single-use (no replay), ctx-bound (can't be repurposed), and short-lived (10 min). The signing seed is config/SOPS, never logged. No secrets in tokens (only jti/ctx-hash/tier/exp).

**Deferred (disclosed):** **campaign-executor** verification — no campaign executor exists yet (latent); the **proxy token-attach** is at the send caller (normalizer today, the approval-execution flow later) since no side-effecting tool runs through the proxy at tier<2 yet; the **daily jti prune** job awaits the scheduler entrypoint (#16). The approval-object lifecycle (create→notify→approve→execute) remains a later ticket.

---

## 2026-08-03 — MVP-067 · Approval service + resolve API

**Ticket:** [MVP-067](../docs/tickets/MVP-067.md) · P0 · "M". Branch `feature/mvp-067-approval-service` (off main). *"An owner approves/rejects/edits pending actions; edits are re-evaluated."* First of the founder-approved 067-then-069 pair.

**Files (new):** `core/approvals/service.py`, `core/approvals/api.py`, `migrations/versions/9f90c8831001_approvals_object.py`, `tests/integration/test_approval_service.py`. **(modified):** `core/api/main.py` (approvals router).

**Migration** (`9f90c8831001`, revises `1993ba538f4f`): the `approvals` object table (+RLS) — listed under migration 014 in the order doc but split out as the next revision (founder-approved 2026-08-03, DECISIONS); additive, no FK conflict. Columns include `run_id` (the parked run for MVP-069), `payload`/`edited_payload`, `matched_rules`, `audit_id` (the resumed side effect's capability), `status`, `expires_at`. Round-tripped; `make db-roles` re-applied.

**service.py** — `create_approval(...)` inserts a pending row and emits `approval.requested.v1` (preview + timeout). `resolve(...)` locks the row `FOR UPDATE`: a **double-tap** finds `status != pending` and returns the first outcome (`idempotent_replay=True`) with no second event; an **expired** row is marked `expired` and raises `ApprovalExpired` (→ 410); an **approve-with-edit** re-runs `engine.evaluate` on the edited payload — if the new tier **exceeds the approval's tier** the resolve is turned into a **rejection with an explanation** (the ceiling is the authority already granted; an escalated edit needs fresh, higher approval), else the edit is approved. Emits `approval.resolved.v1` (the MVP-069 trigger). `list_approvals` is the queue.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| create → announce; list the queue | `test_approval_service::test_create_lists_and_announces` | PASS |
| approve/reject emit `approval.resolved` | `::test_approve_then_resolved_event`, `_reject` | PASS |
| **double-resolve idempotent** (first outcome, one event) | `::test_double_resolve_is_idempotent` | PASS |
| **edit raises tier → rejected with explanation** | `::test_edit_raising_tier_is_rejected_with_explanation` | PASS |
| edit same tier → approved | `::test_edit_same_tier_is_approved` | PASS |
| **expired → 410** | `::test_resolve_expired_is_410` | PASS |

**Commands:** ruff · mypy core (**92 files**) · guards (6) · alembic up/down/up + `make db-roles` · `pytest -q` **449 passed, 0 skipped** (+7). `GET /v1/approvals` + `POST /v1/approvals/{id}/resolve` in the OpenAPI; approval events already registered (no vault change).

**Commit:** merge `bd84d39` (pushed to `origin/main`).

**Security:** `approvals` is org-scoped (+RLS forced); resolve is `APPROVALS_RESOLVE` (staff can read, not resolve — RBAC); an edit cannot lower the authority bar (re-evaluated, escalation rejected); idempotent under concurrency (`FOR UPDATE`).

**Out of scope (ticket):** owner **notification** (WhatsApp interactive + escalation ladder) → MVP-068; the **parked-run resume** (executor parks on `ApprovalPending`, resumes on `approval.resolved`, wires `tool_call`→proxy) → **MVP-069**, next.

---

## 2026-08-03 — MVP-069 · Approval-parked run resume (the runtime half of the tier-2 loop)

**Ticket:** [MVP-069](../docs/tickets/MVP-069.md) · P0 · "M". Branch `feature/mvp-069-parked-resume` (off main). *"A parked run resumes exactly where it stopped with exactly one side effect."* Second of the founder-approved 067-then-069 pair; closes the mediation→approval→resume loop. **No migration.**

**Files (new):** `core/runtime/resume.py`, `tests/integration/test_executor_approval.py`. **(modified):** `core/mediation/proxy.py` (`RunContext.approved` + tier-skip for an approved tool), `core/runtime/executor.py` (proxy-backed tool path + park + `resume_after_approval`).

**Executor → proxy.** The `tool_call` node's default `execute_tool` now calls `proxy.call` (built from the instance's permission manifest in `start_run`/`resume_run`); it returns a status dict the driver reads — `pending` parks, `aborted` interrupts, `ok`/`error` continue. `deps` still fully overrides for hermetic tests (the MVP-055 chaos/happy tests inject deps and are untouched); `model`/`respond` override just those node behaviours over the real proxy tool.

**Park.** `_park` creates the approval (MVP-067) linked to `run_id`, then checkpoints with the cursor **left at the node before `tool_call`** and `pending_tool` restored (and the un-executed attempt rolled back), so a resume re-issues the *same* call; the run interrupts.

**Resume.** `resume_after_approval(run_id, org, decision)` reloads the checkpoint; it is **idempotent** — a run that is no longer `interrupted` is a no-op, so a double-resolve resumes once. **approve** rebuilds the `RunContext` with `approved={tool}` (the proxy skips the tier gate for it) and re-drives → the parked tool executes exactly once → the run completes. **reject** routes straight to a customer-safe close (`SAFE_CLOSE_TEXT`); the original action never runs. `resume.py` registers a consumer on `approval.resolved.v1` (org from the CloudEvents `subject`) that finds the parked `run_id` and drives the resume; the consumer framework dedupes per event id and the run-status guard makes it exactly-once.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| tier-2 tool → **parks** (approval row created, tool not executed) | `test_executor_approval::test_tier2_tool_parks_the_run` | PASS |
| **approve → exactly one execution**, run succeeds | `::test_approve_resumes_and_executes_exactly_once` | PASS |
| **reject → customer-safe close, original never runs** | `::test_reject_closes_safe_without_executing` | PASS |
| **double-resolve → single resume** | `::test_double_resolve_resumes_once` | PASS |
| resume consumer wires `approval.resolved` → resume | `::test_resume_consumer_wires_resolved_event` | PASS |

**Commands:** ruff · mypy core (**93 files**) · guards (6, incl. runtime-not-tools — the executor imports `core.mediation.proxy` + `core.approvals.service`, never a tool impl) · `pytest -q` **454 passed, 0 skipped** (+5). MVP-055 chaos/isolation tests stay green.

**Commit:** merge `4d63e69` (pushed to `origin/main`).

**Security:** the tier-2 contract is now enforced end-to-end — a side effect that needs approval cannot run until an owner resolves it; the approved tool skips only *its own* tier gate (all other proxy checks still apply); resume is exactly-once (dedupe + run-status + the single-use jti of MVP-066 on any real send).

**Deferred (disclosed):** registering the resume consumer **in the worker** process (registration is by import; the worker/scheduler entrypoint is still the MVP-028 placeholder, cf. #16); the parked-tool → **real send** wiring (the `messages.send` registry impl still routes to the approval flow — executing an approved `messages.send` through the MVP-054 gates + a minted MVP-066 token is the remaining integration; exactly-once is proven here with a benign tool + the terminal respond).

---

## 2026-08-03 — MVP-068 · WhatsApp interactive approvals + escalation ladder

**Ticket:** [MVP-068](../docs/tickets/MVP-068.md) · P0 · "M". Branch `feature/mvp-068-whatsapp-approvals` (off main). *"An owner approves from a WhatsApp tap in seconds."* The owner-notification delivery + inbound routing for the MVP-067 approval object.

**Files (new):** `core/approvals/notify.py`, `migrations/versions/bb65660f0771_approvals_notify_state.py`, `tests/unit/test_approval_notify.py`, `tests/integration/test_approval_notify_db.py`. **(modified):** `project-management/DECISIONS.md`.

**Migration** (`bb65660f0771`, revises `9f90c8831001`): adds `notified_at`/`reminded_at`/`escalated_at`/`notify_ref`/`notify_channel` to `approvals`. **Flagged deviation:** the ticket said "Database changes: None — approvals table (014) carries notification state columns already," but they never existed (nor in schema.sql); the ladder needs them. Additive; RLS already on the table; round-tripped; `make db-roles` re-applied. Founder pre-approved (DECISIONS 2026-08-03).

**notify.py** — `render_card(action_type, payload)` is a text form of the pack commitment card (breakdown lines + total); `compose_interactive(approval_id, body)` builds the ✅ Approve / ❌ Reject buttons carrying `approve:<id>` / `reject:<id>`. `notify_approval` renders, sends via a **gated-simulated `SimulatedNotifier`** (Meta not live), and stamps the ladder column. A consumer on `approval.requested.v1` (org from the CloudEvents `subject`) notifies the owner. **Reply routing:** `parse_button` + `parse_text_decision` (✅/❌ and approve/reject/yes/no/haan/nahi — the Meta-template hedge; ambiguous or both → no action) feed `handle_button_reply` / `handle_text_reply` → `service.resolve` (text resolves the org's latest pending). **Ladder** `run_approval_ladder` (registered every minute via `register_jobs`, per-org fan-out like the embeddings batch): **remind** at 50% of the window, **escalate** at 75%, **expire** (safe-hold) at the deadline — one transition per tick, most-advanced first.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| button routing (`approve:`/`reject:` → decision; non-approval ignored) | `test_approval_notify::test_parse_button_*` | PASS |
| text fallback parser (✅/❌/words; ambiguous → none) | `::test_text_fallback_parses_decision`, `_is_none_when_ambiguous` | PASS |
| card render + interactive compose | `::test_render_card_*`, `_compose_interactive_*` | PASS |
| notify stamps `notified_at` + sends; requested-consumer notifies | `test_approval_notify_db::test_notify_stamps_and_sends`, `_requested_consumer_notifies` | PASS |
| button + text reply resolve (latest pending for text) | `::test_button_reply_resolves`, `_text_reply_resolves_latest_pending` | PASS |
| **ladder fires remind → escalate → expire on schedule** (ap-10) | `::test_ladder_remind_escalate_expire` | PASS |

**Commands:** ruff · mypy core (**94 files**) · guards (6) · alembic up/down/up + `make db-roles` · `pytest -q` **478 passed, 0 skipped** (+24).

**Commit:** merge `22023dd` (pushed to `origin/main`).

**Security:** notification content is templated (no LLM); reply routing resolves through the same `service.resolve` (RLS-scoped, `FOR UPDATE` idempotent, edit-re-eval); notifications carry no secrets (a rendered preview + the approval id).

**Deferred (disclosed):** real WhatsApp **interactive send** via Meta (gated) + the `tap→sent <10s` staging measurement; **scheduler firing** of the ladder (entrypoint is the MVP-028 placeholder, #16); the webhook **normalizer** inbound button/text → `handle_*_reply` wiring; the full pack **`commitment_card`** layout render (text summary now); the **backup-approver** identity/routing on escalate (currently re-notifies the same owner channel).

---

## 2026-08-03 — MVP-070 · Trust ledger job (earned autonomy bookkeeping)

**Ticket:** [MVP-070](../docs/tickets/MVP-070.md) · P1 · "S". Branch `feature/mvp-070-trust-ledger` (off main). *"Clean approvals accumulate; incidents reset and tighten."* The scheduler counterpart to the policy engine's `trust_ledger`/`incident_tightening` tables (MVP-065).

**Files (new):** `core/approvals/trust.py`, `migrations/versions/30b7edf76a9d_approvals_trust_settled.py`, `tests/integration/test_trust_ledger.py`. **(modified):** `project-management/DECISIONS.md`.

**Migration** (`30b7edf76a9d`, revises `bb65660f0771`): `approvals.trust_settled boolean NOT NULL DEFAULT false` + a partial index on the unsettled tier-2 set. **Flagged deviation** — the ticket said DB = "trust_ledger rows"; the marker is required so the hourly increment is idempotent (a per-run watermark alternative is more fragile). Additive; RLS already on the table; round-tripped; `make db-roles` re-applied. Founder pre-approved (DECISIONS 2026-08-03).

**trust.py** — `settle(session, org, now?)`: over tier-2 approvals with `status='approved' AND NOT trust_settled AND decided_at + 72h <= now`, add +1 to `clean_approvals` for the action type **iff** no incident touched it in `[decided_at, decided_at+72h]` (via `trust_ledger.last_incident_at`), and mark each `trust_settled` (counted once). `record_incident(session, org, action_type, reason?, now?)`: reset `clean_approvals=0` + stamp `last_incident_at`, and insert a self-expiring `incident_tightening` row (tier 2, +14 days) that the engine already honours. `demotion_offers(session, org)`: action types at/over the threshold → a `loosen_one_tier` offer marked `requires: owner_approval` — **read-only**, for the digest; never writes a policy (IDL-007). `run_trust_settle` registered hourly (`register_jobs`), per-org fan-out.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| +1 per tier-2 approval past a clean 72h window; **idempotent**; skips inside the window | `test_trust_ledger::test_settle_increments_a_72h_clean_approval`, `_settle_is_idempotent`, `_settle_skips_inside_the_window` | PASS |
| **ap-12** incident → reset + 14-day self-expiring tightening | `::test_incident_resets_and_writes_14d_tightening` | PASS |
| **72h boundary** — an incident at 71h59m blocks the increment | `::test_72h_boundary_incident_at_71h59m_blocks_increment` | PASS |
| **ap-11** demotion offer is digest-only, never auto-applied (no policy written) | `::test_demotion_offer_is_digest_only_never_applied` | PASS |

**Commands:** ruff · mypy core (**95 files**) · guards (6) · alembic up/down/up + `make db-roles` · `pytest -q` **484 passed, 0 skipped** (+6).

**Commit:** merge `ef0ce5e` (pushed to `origin/main`).

**Security:** pure policy bookkeeping (no agent/customer surface); autonomy only ever **tightens** automatically (incident → tier 2, 14d) — loosening is offer-only + owner-approved (IDL-007); RLS scopes all reads/writes to the org.

**Deferred (disclosed):** the **incident detector** that calls `record_incident` (out of scope — the incident signal is an input); scheduler **firing** of the hourly job (#16); the pack-configurable demotion **threshold** (constant 20 now); the **digest** surface that renders the offers (insights, later); the demotion-**apply** meta-approval flow (out of scope per ticket).

---

## 2026-08-03 — MVP-061 · Manifest compiler + signing (real manifest verification)

**Ticket:** [MVP-061](../docs/tickets/MVP-061.md) · P0 · "M". Branch `feature/mvp-061-manifest-compiler` (off main). *"An instance's allowed tool surface is compiled, signed, and pinned to every run."* Makes the mediation proxy's manifest verification real (MVP-060 deferred the signature). **No new migration** (`agent_instances.permission_manifest` exists since 008).

**Files (new):** `core/mediation/manifest.py`, `tests/unit/test_manifest.py`, `tests/integration/test_manifest_compiler.py`. **(modified):** `core/common/config.py` (`manifest_signing_seed`), `core/mediation/proxy.py` (verify sig + hash + freshness; dropped the hash-only `_manifest_hash`), `core/runtime/executor.py` (pin the body hash via `manifest.manifest_hash`), `tests/integration/test_mediation_proxy.py` + `test_executor_approval.py` (sign their manifests).

**manifest.py** — `compile_manifest(*, instance_id, org_id, allowlist, tool_grants, tier_defaults?, budgets?, tenant_allow?)` intersects **L1 archetype `capability_allowlist` ∩ L2 pack `tool_grants` ∩ L3 tenant** (optional): a read-only tool (`.read`/`.search`) gets `read_only`, every other granted tool `requires_tier_eval`; `untrusted_narrowing.allow` = the read-only set. `sign(body)` adds `hash` (`sha256:` of the canonical **body** — excludes hash/sig) + `signature` (`ed25519:` over the body); `verify` checks both. `manifest_hash` is the body hash (what gets pinned). `recompile_instance` reloads grants (archetype ∩ binding), compiles, signs, and re-pins `agent_instances.permission_manifest`.

**Proxy (step 1, hardened):** the run's `manifest_hash` must equal `manifest_hash(ctx.manifest)`, the manifest's own hash must match its body, the **ed25519 signature** must verify, and the pin must be **fresh** — equal to the instance's *current* compiled manifest hash (a grant change recompiles → an old pin is stale → denied until re-pinned). The freshness lookup is skipped when the instance isn't persisted (hermetic proxy tests). Any failure → `permission_denied_manifest` + a violation; ≥3 → `RunAborted`. The executor now pins the **body** hash so it matches.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| intersection (archetype ∩ pack) + tenant narrowing; read-only vs tier-eval | `test_manifest::test_intersection_*`, `_tenant_grants_narrow_further`, `_read_only_skips_*` | PASS |
| ed25519 sign/verify roundtrip; **any tamper fails verify** | `::test_sign_then_verify_roundtrips`, `_tampering_any_part_fails_verify` | PASS |
| recompile pins a signed intersection on the instance | `test_manifest_compiler::test_recompile_pins_a_signed_intersection` | PASS |
| **stale manifest after (re)compile → denied until re-pinned** | `::test_stale_manifest_denied_until_recompile` | PASS |
| **forged/tampered manifest → sig fail → denied, run aborts after 3** | `::test_tampered_manifest_denied_and_aborts_after_three` | PASS |

**Commands:** ruff · mypy core (**96 files**) · guards (6) · `pytest -q` **493 passed, 0 skipped** (+9). MVP-060/069 tests migrated to signed manifests and stay green.

**Commit:** merge `870d565` (pushed to `origin/main`).

**Security:** the permission manifest is now unforgeable (ed25519, platform key from SOPS) and un-stale-able (freshness pin) — the proxy trusts it on every call; the executor pins the body hash into each run; a tamper aborts the run. Read-only tools skip the tier gate; everything else consults the engine.

**Deferred (disclosed):** the **level-3 tenant-grants source** (no tenant-grants table/UI — `tenant_allow` narrowing is a no-op by default); the **automatic recompile-on-grant-change** trigger (`recompile_instance` exists; firing it on a grant change is the seam — AC "recompile on grant change is automatic" is partial); budgets sourced from `budget_caps` (may lack tokens/spend/sends-day keys); `requires_tier_eval`/`read_only` by name heuristic (no explicit grant flag in `ToolGrant`).

---

## 2026-08-03 — MVP-062 · Budgets, rate windows, untrusted narrowing (blast-radius controls)

**Ticket:** [MVP-062](../docs/tickets/MVP-062.md) · P0 · "M". Branch `feature/mvp-062-limits` (off main). *"Cap spend/sends and shrink the tool surface after untrusted content."* Hardens the manifest-driven proxy checks that MVP-061 now feeds real signed data into. **No Postgres migration** (Redis with daily-key TTLs).

**Files (new):** `core/mediation/limits.py`, `tests/unit/test_limits.py`. **(modified):** `core/mediation/proxy.py` (steps 3/5/6 + mark-after-untrusted-result; dropped the fixed-window `_rate_ok`/read-only `_budget_ok`), `core/runtime/executor.py` (`resume_after_approval` clears the untrusted flag), `tests/integration/test_mediation_proxy.py` (FakeRedis gained sorted-set ops + a narrowing test).

**limits.py** — `check_rate` is a **true 60s sliding window** per (instance, tool): a Redis sorted set of timestamps, prune older than 60s, count, deny at the cap (a denied call consumes no slot — accurate, unlike the old fixed-minute bucket). `check_budget`/`record_budget` are per-(instance, kind) **daily counters** with a 2-day TTL — the proxy checks the cap, the side-effect boundary records usage. The **narrowing lifecycle**: `result_is_untrusted(tool, result)` (a tool in `{web_fetch, file_ingest, forwarded_content}` or a `content_class=external_untrusted` result), `mark_untrusted`/`is_untrusted`/`clear_untrusted` per run.

**Proxy wiring:** step 3 narrowing now denies when `ctx.untrusted OR is_untrusted(run)` and the tool isn't on `untrusted_narrowing.allow`; step 5 uses the sliding window; step 6 checks the daily send cap for `messages.send` (logging a breach for telemetry); after execution, a tool that returned external content marks the run untrusted. The executor's `resume_after_approval` clears the flag (approval = human boundary; a new customer turn is a fresh run).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| **sliding-window accuracy** — burst capped, allowed after it slides | `test_limits::test_sliding_window_caps_a_burst_then_allows_after_it_slides` | PASS |
| daily budget check / record / exhaustion | `::test_budget_check_and_record_and_exhaustion` | PASS |
| narrowing mark→is→clear lifecycle; result classification | `::test_untrusted_lifecycle_mark_is_clear`, `_result_is_untrusted_*` | PASS |
| **AC: web_fetch (untrusted) → messages.send denied; catalog.search (allow-listed) allowed** | `test_mediation_proxy::test_untrusted_content_narrows_subsequent_tools` | PASS |

**Commands:** ruff · mypy core (**97 files**) · guards (6) · `pytest -q` **500 passed, 0 skipped** (+7). MVP-060/061/069 proxy+executor tests stay green.

**Commit:** merge `3752c3f` (pushed to `origin/main`).

**Security:** ib-08 structural defence — a run that ingests external content can only use the narrowing allow-list (indirect-injection containment) until a human boundary; sliding-window rate + daily send cap bound the blast radius; breaches carry no customer data (instance + kind + cap).

**Deferred (disclosed):** the budget **record** at the real send boundary (in the `messages.send` tool impl once it's wired to the MVP-054 send path — same seam as MVP-069); the `telemetry_events` dashboard table (breach → structured log now); **tokens/spend** daily budgets (sends is the hard external cap wired; tokens/spend are run-level on `agent_runs`).

---

## 2026-08-04 — MVP-063 · Failure contract + circuit breaker

**Branch:** `feature/mvp-063-failure-circuit` (off main). **Commit:** merge `288427e` (pushed to `origin/main`).

**Objective:** a failing agent pauses itself loudly instead of flailing at customers — a step failure is retried once; two consecutive failures open the circuit (instance `circuit_open`, owner alert, incident); a tier-2 failure auto-opens an incident with the run link and tightens autonomy; a manual resume drains held work.

**Migration `da3474bd3cdb` (incidents).** Creates the org-scoped `incidents` table (+RLS, forced, 2 policies) — `org_id`, `run_id`→`agent_runs` (SET NULL), `instance_id`→`agent_instances` (SET NULL), `kind`, `severity`, `title`, `action_type`, `detail jsonb`, `status` (`open`/`resolved`), `opened_at`, `closed_at`; two indexes (open-by-org, by-run). Lands ahead of its scheduled slot (018/MVP-074) — additive, flagged (DECISIONS 2026-08-04). `circuit_open` was already an allowed `agent_instances.status` value → **no status migration**. Upgrade + downgrade both verified; `make db-roles` re-applied.

**core/runtime/failure.py (new).** The breaker state machine: consecutive-failure count in Redis (per instance, 1h TTL), incidents + instance status in Postgres (RLS-scoped). `note_failure` — tier-2+ auto-opens a `tier2_failure` incident (with the run link) + `trust.record_incident` (reset + 14d tighten, MVP-070); increments the streak; the 2nd consecutive failure opens the circuit. `_open_circuit` sets the instance `circuit_open`, writes a `circuit_open` incident, and fires `alert.ops.v1`. `note_success` resets the streak. `is_circuit_open` reads the instance status. `close_circuit` (manual resume) clears the counter, reactivates the instance, and resolves the open circuit incident so held conversations drain.

**Executor wiring (`core/runtime/executor.py`).** `start_run` **holds** when the instance's circuit is open — it records an interrupted run (`circuit_open`) and returns without driving (planner hold). The `_drive` loop now handles a hard (`provider_unavailable`) tool result: `note_failure` counts it, the step is **retried once in place** (re-issues the same tool without re-consulting the model), and the 2nd consecutive failure opens the circuit and interrupts the run; a clean tool result calls `note_success`. `_make_proxy_tool` tags the tool's consequence tier onto an error result (tier-eval tools → 2, read tools → 1) so a failed tier-2 send is classified for the incident path.

**Proxy (`core/mediation/proxy.py`).** The execute step now wraps the tool call: an implementation that raises becomes a structured, recoverable `provider_unavailable` `ToolResult` (the failure contract) instead of propagating out of the run.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| forced double (consecutive) failure → circuit_open + owner alert | `test_runtime_failure::test_second_consecutive_failure_opens_circuit_with_alert` | PASS |
| tier ≥ 2 failure → incident row **with run link** + autonomy tightened | `::test_tier2_failure_opens_incident_and_tightens` | PASS |
| a clean step resets the streak (transient failure ≠ trip) | `::test_clean_step_resets_the_counter` | PASS |
| recovery: `close_circuit` reactivates the instance + resolves the incident | `::test_close_circuit_reactivates_and_resolves` | PASS |
| **end-to-end**: persistently-failing tool retried once → breaker trips → run interrupts; next run **held**; drives again after recovery | `::test_persistent_tool_failure_trips_breaker_then_holds` | PASS |
| `incidents` tenant isolation (own rows only; fail-closed without context) as `app_rw` | `tests/isolation/test_incidents_rls::test_incidents_isolated_under_app_rw` | PASS |

**Commands:** `ruff check .` (pass) · `mypy core` (**98 files**, pass) · `mypy migrations` (pass) · guards **17 passed** (runtime-not-tools clean — `failure.py` imports only `core.approvals.trust` + `core.tenancy.repository`) · `alembic upgrade/downgrade` round-trip (pass) · `pytest -q` **506 passed, 0 skipped** (+6 vs MVP-062's 500). MVP-055/060/061/069 executor+proxy suites stay green.

**Security:** the breaker is a blast-radius control — a provider-failing instance stops driving customer-facing work after two attempts; incidents + status are RLS-scoped; the `alert.ops` payload carries only ids (no customer data). Provider exceptions no longer crash the run (fail-closed to a recorded, recoverable failure).

**Deferred (disclosed):** the **<30s alert-delivery** measurement (the alert is emitted immediately to `alert.ops.v1`; the ops-dashboard/notification consumer that surfaces it to the owner is #16 scheduler/worker wiring); the **incident-detector → `record_incident`** path for non-runtime incidents (an input, out of scope); the pack-configurable **retry/threshold** (constants `STEP_RETRY_LIMIT=1`, `CIRCUIT_THRESHOLD=2`); migration **018 (MVP-074) must skip** re-creating `incidents`.

**Next recommended action:** founder review + approve commit/merge/push; then MVP-064 (model routes + failover) or the worker/scheduler wiring (#16).
