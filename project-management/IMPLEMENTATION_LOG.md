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
