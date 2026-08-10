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

---

## 2026-08-04 — #16 · Worker + scheduler process entrypoints (over MVP-026 / MVP-028 frameworks)

**Branch:** `feature/mvp-028-scheduler-worker-entrypoints` (off main). **Commit:** merge `ac4ab52` (pushed to `origin/main`).

**Objective:** the consumer/scheduler/outbox frameworks shipped and were tested (MVP-026/027/028), but the two process entrypoints (`core/worker.py`, `core/scheduler.py`) were still `sleep(3600)` placeholders — so no registered consumer, job, or outbox event ever fired (BLOCKER #16). Wire the entrypoints. **No migration, no dependency.**

**`core/worker.py`.** `_install_consumers()` imports the three `@consumer` modules (import registers them; idempotent via the module cache): the `msg.received` logger, `approval.requested → notify_approval` (gated-simulated notifier), `approval.resolved → resume_after_approval`. `run_worker(stop, redis=None, consumer_name=None)` launches `run_publisher(stop)` (outbox → Redis-streams relay) + one `run_consumer` per registered handler and `gather`s them; `main()` installs SIGINT/SIGTERM handlers that set the stop event. Graceful: the framework finishes the in-flight batch and acks before exiting (no ack loss).

**`core/scheduler.py`.** `_install_jobs()` calls `scheduler.clear()` then registers the canonical set — `approval_ladder` (`* * * * *`), `trust_ledger_settle` (`0 * * * *`), `embeddings_batch` (`*/5 * * * *`, `SimulatedEmbedder`), `dedupe_prune` (`30 3 * * *`, new daily maintenance wrapper over `consumer.prune_dedupe`). `run_scheduler_process(stop, redis=None, tick_s=60)` ticks `run_scheduler` under the per-(job, minute) Redis lock. Same graceful-signal `main()`.

**`core/events/scheduler.py`.** Added a one-line public `clear()` so the entrypoint installs its job set authoritatively and idempotently across (re)starts.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| worker registers every `@consumer` handler (logger, approval-notify, runtime-resume) | `test_worker_entrypoint::test_install_consumers_registers_all_handlers` | PASS |
| worker assembles publisher + consumers and stops gracefully | `::test_run_worker_assembles_and_stops_gracefully` | PASS |
| scheduler installs the full job set (ladder/trust/embeddings/prune) | `test_scheduler_entrypoint::test_install_jobs_registers_the_full_set` | PASS |
| **lock proof** — installed jobs fire once at their minute; a 2nd scheduler that minute is a no-op | `::test_installed_jobs_fire_once_under_the_lock` | PASS |
| scheduler installs + ticks + stops gracefully | `::test_run_scheduler_process_installs_and_stops_gracefully` | PASS |
| **live-broker boot smoke** — worker (3 consumers + publisher) + scheduler (4 jobs, `approval_ladder` fired) boot against real Redis/DB and shut down cleanly | manual smoke (logged `job ok: approval_ladder`) | PASS |

**Commands:** `ruff check .` (pass) · `mypy core` (98 files, pass) · scaffold import guard (worker/scheduler import clean) · `pytest -q` **511 passed, 0 skipped** (+5) · live-broker boot smoke (pass).

**Security / side effects:** every wired runner is internal (Redis streams, DB) or **gated-simulated** (notifier, embedder) — the worker/scheduler perform no real external action. Jobs fan out per org through `org_scoped_session` (RLS preserved). Graceful shutdown prevents ack loss.

**Deferred (disclosed):** the real **embedding provider** (BLOCKER #16 stays open — founder picks provider + approves dep/creds; the batch is wired, only the simulated→real `Embedder` swap remains); the `jobs_runs` observability table (MVP-028 already deferred — structured logs cover the acceptance); the **flags fast-path subscriber** loop (not built — the executor loads flags per-run via `load_snapshot`, so the kill switch works, just not sub-2s push); the docker-compose **env-var prefix mismatch** (BLOCKER #1) that a full app-container `make dev` boot needs.

**Next recommended action:** founder review + approve commit/merge/push; then MVP-064 (model routes + failover).

---

## 2026-08-04 — MVP-064 · Model routes + failover (Option A, gated-simulated)

**Branch:** `feature/mvp-064-model-routes-failover` (off main). **Commit:** merge `232f8e5` (pushed to `origin/main`).

**Objective:** each task class uses the right model with a resilient failover chain — primary → secondary → holding template — with per-route/run cost logging. Built over simulated providers (the LLM stays gated-simulated per the 2026-08-02 decision); real vendors drop in at go-live with no change to the routing code. **No dependency.**

**Migration `3680972ace7a` (costs_lite + model_routes seed).** `costs_lite` — org-scoped (+RLS, forced, 2 policies): `run_id`→`agent_runs` (SET NULL), `node_key`, `provider`, `model`, `outcome` (ok/failed), `tokens_in/out`, `cost_usd numeric(10,6)`, index on `(org_id, run_id, created_at)`. Lands ahead of the migration-order doc (additive, flagged — DECISIONS 2026-08-04). Idempotent **seed** of `model_routes` (created global in 015): `default`, `classify`, `converse`, `campaign`, each with an `anthropic` primary + `openai` fallback. Upgrade + downgrade verified; `make db-roles` re-applied.

**core/runtime/model.py.** Added the provider layer: a `Provider` protocol (`complete(node_key, prompt, context, model, params)`), a `SimulatedProvider(name)` (deterministic, no cost — mirrors `SimulatedModel`), and `get_provider(name)` — the **gated seam**: until `llm_provider_enabled` every provider name resolves to the simulated client; at go-live real clients register in `_REAL_PROVIDERS` (fail closed until wired).

**core/runtime/routing.py (new).** `RoutingModel` (a `Model`): per turn it loads the `model_routes` row for the `node_key` (falling back to the seeded `default`, then a hard-coded fail-safe chain), walks **primary → fallbacks**, returns the first provider that answers, and logs each attempt to `costs_lite` (route + run attribution). If **every** provider fails it returns the **holding template** — a static no-tool reply that closes the turn with zero successful LLM output — and emits an `alert.ops`. `_estimate_cost` applies the placeholder per-provider price.

**Executor wiring.** `start_run`, `resume_run`, and `resume_after_approval` now build `RoutingModel(org_id, run_id, redis)` where they previously used `default_model()` — so production runs route + log cost; an injected `model=`/`deps=` (all runtime tests) still overrides, so the existing executor suites are untouched.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| primary 500 → secondary transparently (same turn succeeds) | `test_model_routing::test_primary_failure_fails_over_to_secondary` | PASS |
| all-down → holding template, zero successful LLM calls, alert emitted | `::test_all_providers_down_returns_holding_template_and_alerts` | PASS |
| cost rows attribute to the correct route + run | `::test_cost_row_attributes_to_run_and_route` | PASS |
| unrouted node_key resolves via the seeded default chain | `::test_unrouted_node_key_uses_the_default_chain` | PASS |
| per-provider cost estimate (+ default/zero) | `test_routing_cost::test_estimate_cost_*` | PASS |
| `costs_lite` tenant isolation (own rows; fail-closed) as `app_rw` | `test_costs_lite_rls::test_costs_lite_isolated_under_app_rw` | PASS |
| **end-to-end**: `start_run` (no model injected) → executor builds RoutingModel → routes + logs cost rows | live smoke (run succeeded, 2 `costs_lite` rows attributed to the run) | PASS |

**Commands:** `ruff check .` (pass) · `mypy core` (99 files, pass) · `mypy migrations` (pass) · `alembic upgrade/downgrade` round-trip (pass) · `pytest -q` **518 passed, 0 skipped** (+7) · live executor→routing smoke (pass). MVP-055/063/069 executor suites stay green (they inject models).

**Security / side effects:** no real external call — providers are gated-simulated (no vendor, no key, no spend). `costs_lite` is org-scoped (+RLS) — a tenant's cost/usage is tenant data; cross-tenant isolation tested. The all-down path fails safe (holding template, no fabricated content) + alerts.

**Deferred (disclosed):** the real vendor clients (go-live — register in `_REAL_PROVIDERS` behind `llm_provider_enabled`); real per-token **pricing** (placeholder estimate now); **dynamic routing** (out of scope per ticket — static routes only); routing on a **task-class** node_key (the graph passes the constant `priya.reason`, which resolves via `default`; wiring per-class node keys into `model_turn` is a follow-up); a costs **dashboard/rollup** (rows are written; the digest surface is later, insights).

**Next recommended action:** founder review + approve commit/merge/push.

---

## 2026-08-04 — MVP-056 · Planner routing

**Branch:** `feature/mvp-056-planner-routing` (off main). **Commit:** merge `e1b274c` (pushed to `origin/main`).

**Objective:** turn a real inbound customer message into a routed agent run — classify intent, resolve to archetype+task via the pack taxonomy, apply three global guards, enqueue the run; unclassifiable → concierge+clarify. **No migration, no dependency** (reads the pack + bindings). Connects the already-built inbound channel → the executor/proxy/approvals spine.

**`verticals/jewelry/agents/bindings.yaml`.** Added `planner.intent_keywords` — a declarative intent→keywords map (16 intents) for the classifier. Pack authoring (declarative, in-repo, not the vault). `contact_frequency_cap` was already present.

**`core/packs/taxonomy.py` (new).** `load_taxonomy(slug)` reads the pack's `agents/bindings.yaml` (via `BindingsPack`), building `intent → Route(archetype, task)` + `intent_keywords` + `frequency_cap` + the concierge fallback. Loaded through the pack layer so `core/` never imports `verticals/` (Rule Zero).

**`core/runtime/planner.py` (new).** The `@consumer` on `msg.received.v1` (`_handle` is the injectable core; the consumer is a thin wrapper). Pipeline: **classify** (`classify` — longest pack-keyword match wins, gated-simulated) → **route** (`route_message` — intent→archetype/task, else concierge+clarify) → **guards** (`is_tenant_paused` = org status ≠ active; `suppression_blocks` = mirror the send-path rule, `all` blocks everything and `marketing` blocks only marketing-class; `frequency_cap_blocks` = daily per-contact cap, transactional/active-conversation exempt) → **enqueue** (`start_run` against the org's active instance for the routed archetype, `trigger=msg.received`, `input={body,intent,task,clarify}`). Returns a short outcome (`enqueued`/`paused`/`suppressed`/`capped`/`no_pack`/`no_instance`). `record_marketing_touch` counts a marketing touch against the cap (the send path calls it; the planner reads it).

**`core/worker.py`.** `_install_consumers` now imports `core.runtime.planner`, so the worker runs the planner consumer.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| **routing_golden 20/20** — 20 messages route to the right archetype+task | `test_planner_routing::test_routing_golden_20` | PASS |
| unclassifiable → concierge + clarify fallback | `::test_unclassifiable_falls_back_to_concierge_clarify` | PASS |
| classifier longest-keyword-wins / no-match | `::test_classify_longest_keyword_wins`, `_no_keyword_returns_none` | PASS |
| **cap blocks second marketing touch same day** | `test_planner_guards::test_cap_blocks_second_marketing_touch_same_day` | PASS |
| guard matrix — paused / suppression (all vs marketing) / cap exemptions | `test_planner_guards::*` (7) | PASS |
| consumer path — inbound enqueues run to active concierge instance | `test_planner_consumer::test_inbound_message_enqueues_run_to_concierge` | PASS |
| paused tenant / fully-suppressed contact / no active instance all drop | `test_planner_consumer::test_{paused_tenant,fully_suppressed_contact,no_active_instance}_drops` | PASS |

**Commands:** `ruff check .` (pass) · `mypy core` (101 files, pass) · guards (24, runtime-not-tools clean) · `pytest -q` **534 passed, 0 skipped** (+16) · live smoke (worker registers the planner consumer on `msg.received.v1`; taxonomy loads 16 routes + cap).

**Security / side effects:** no external action — the planner only *routes*; the run goes through the same mediation/approval/audit spine. Guards fail safe (paused/suppressed/capped → drop, logged). Per-org via `org_scoped_session` (RLS preserved). Classification is gated-simulated (no vendor).

**Deferred (disclosed):** the real small-classifier **model** (go-live, same seam); the `support` **archetype seeding** (referenced by the pack, not in `agent_archetypes` — support routes resolve but find no instance until seeded; pre-existing, out of scope); the send-path call to `record_marketing_touch` (the sender is a later ticket — the counter + guard are wired and tested); multi-pack arbitration (explicitly out of scope).

**Next recommended action:** founder review + approve commit/merge/push; then continue the inquiry→draft spine (grounded draft generation) or the imports track (MVP-076).

---

## 2026-08-05 — MVP-044 · Pack seeding: approval policies (prompt layers already seeded)

**Branch:** `feature/mvp-044-pack-seeding` (off main). **Commit:** merge `c27872c` (pushed to `origin/main`).

**Objective:** land the pack's rules + prompt layers in their registries on install — the grounded-draft enabler. **Scope (founder-approved):** prompt-layers + approval-policies; `workflow_definitions` deferred to MVP-072 (016 table not built).

**Finding/correction:** `_seed_prompt_layers` was **already implemented** and the jewelry prompts parse into 9 candidate layers — prompt-layer seeding already worked on install. The real remaining work was `_seed_policies` (a deferred stub).

**`core/packs/installer.py`.** Implemented `_seed_policies`: for every binding's `tier_defaults`, insert an `approval_policies` row (scope='pack', pack_id, `action_type = applies_to` verbatim, tier, `cel_expr = condition`, description, `approver_chain`, `timeout_s`, `on_timeout`, `confirm_kind`), idempotent on (pack, action, description). Helpers `_parse_duration_s` (`30m`→1800) + `_ON_TIMEOUT_MAP` (`hold_and_remind`→`hold`). Removed `policies` from `DEFERRED_STEPS` (now `("workflows",)`); updated the module docstring.

**Migration `b6456b200baa` (approval_policies pack-insert RLS).** Added `CREATE POLICY p_pack_ins FOR INSERT WITH CHECK (org_id IS NULL AND scope='pack')` so the installer (app_rw, in the tenant transaction) can seed **global pack** rows — mirrors `prompt_layers`' `p_layers_ins`, tighter (core rows stay migration-only). Round-tripped; roles re-applied.

**`verticals/jewelry/install.yaml`, `verticals/kirana/install.yaml`.** Updated `expected_result.deferred_steps` → `[workflows]` (policies now seeded). Existing installer/e2e/index/settings fixtures updated to delete `approval_policies` on teardown (new FK `pack_id → packs`).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| **seed matches the pack rules exactly (diff = ∅)** | `test_pack_policy_seed::test_seeded_policies_match_pack_tier_defaults_exactly` | PASS |
| **re-seed idempotent** (no duplicates) | `::test_reseed_is_idempotent` | PASS |
| domain field mapping (30m→1800s, hold_and_remind→hold, approver→chain, condition→cel) | `::test_domain_field_mapping` | PASS |
| **RLS tight** — app_rw seeds `scope='pack'` but NOT `scope='core'` | `::test_app_rw_can_seed_pack_but_not_core_scope` | PASS |
| install seeds 8 pack policies + 9 candidate prompt layers | `test_pack_installer::test_install_seeds_paused_instances_and_candidate_layers` | PASS |
| reference jewelry/kirana install deferred_steps = [workflows] | `test_jewelry_install`, `test_kirana_dryrun` | PASS |

**Commands:** `ruff check .` (pass) · `mypy core` (101) + `mypy migrations` (pass) · `alembic up/down` round-trip (pass) · `pytest -q` **538 passed, 0 skipped** (+4 net) · live smoke (install jewelry → 4 concierge layers w/ real content + 8 tier policies incl. tier-2 quote/tier-3 broadcast).

**Security:** the new RLS policy is **additive + tight** — app_rw may seed only global `scope='pack'` rows; `scope='core'` (platform tier-4 minimums) stays owner/migration-only (tested). Tenant-row isolation unchanged. No secret/PII.

**Deferred (disclosed):** the **tool→action bridge** (BLOCKERS #20) — the pack rules key on abstract actions (`action.quote.send`) but the proxy queries by tool name (`messages.send`), so the seeded policies don't fire on tool calls yet; drafts stay safe (fail-safe tier-2). `workflow_definitions` seeding (MVP-072). Wiring the real **composer (MVP-059) into the executor** so runs use the seeded layers instead of the skeleton prompt (a follow-up; MVP-044 lands the layers, the executor→composer wiring is separate).

**Next recommended action:** founder review + approve commit/merge/push; then the tool→action bridge (make the seeded tiers fire) or the executor→composer wiring (use the seeded layers) to complete grounded drafts.

---

## 2026-08-05 — Tool→action bridge (BLOCKERS #20) — the seeded pack tiers now fire

**Branch:** `feature/tool-action-bridge` (off main). **Commit:** merge `889bb7e` (pushed to `origin/main`). **No migration, no dependency.**

**Objective:** make the MVP-044-seeded pack tier rules take effect. They key on abstract actions (`action.quote.send`), but the proxy asked the engine by tool name (`messages.send`) → no match → everything fail-safed to tier-2 (over-approval).

**`core/approvals/engine.py`.** Added `TOOL_ACTIONS` + `resolve_actions(tool, params)` (a tool → abstract-action family; `messages.send` adds `action.quote.send` when it carries a price — structured `amount_minor` or the largest figure parsed from the body via `core.pricing.extract.extract_amounts`) + `evaluate_tool(...)`. Refactored the rule-matching core into `_contributors(...)` so `evaluate` (single action) and `evaluate_tool` (family) share it; `evaluate_tool` **pools contributors across the family** and applies the empty-set fallback **once** (so a small no-discount quote falls back to the message tier, not "unknown → 2").

**`core/mediation/proxy.py`.** `_engine_tier` now calls `evaluate_tool` (resolving the tool to its action family) instead of querying a single `action_type = tool`.

**`verticals/jewelry/agents/bindings.yaml`.** `has()`-guarded the optional-attribute conditions — `discount_any` (`has(attributes.discount_minor) && …`) and `escalation_triggers` (`has(attributes.sentiment) && …` / `has(attributes.topic) && …`) — so an absent field means "not met" rather than the engine's fail-safe-match.

**Requirement → evidence:**
| Behaviour | Test | Result |
|---|---|---|
| tool→action mapping incl. quote detection | `test_tool_action_bridge::*` (6, unit) | PASS |
| plain reply → tier 1 (auto) | `test_tool_action_bridge_tiers::test_plain_reply_auto_sends_tier1` | PASS |
| ₹1,50,000 quote → tier 2 (approval) | `::test_high_value_quote_needs_approval_tier2` | PASS |
| small no-discount quote → tier 1 | `::test_small_quote_without_discount_auto_sends_tier1` | PASS |
| discounted quote → tier 2 | `::test_discounted_quote_needs_approval_tier2` | PASS |
| broadcast → tier 3 (confirm) | `::test_broadcast_always_confirms_tier3` | PASS |
| **end-to-end through the real proxy** — plain reply sends, ₹1.5L quote parks (ApprovalPending tier 2) | live smoke | PASS |

**Commands:** `ruff check .` (pass) · `mypy core` (101, pass) · `pytest -q` **549 passed, 0 skipped** (+11) · full proxy smoke. MVP-060/065 proxy+engine + MVP-044 diff test (with the `has()`-guarded conditions) stay green.

**Security:** the engine's fail-safe semantics are unchanged (a genuinely broken CEL still tightens); the `has()` guards only make absent *optional* fields resolve to "not met". Platform tier-4 (`payment.*`, `ads.publish`, …) still always → tier 4. No external action; per-org via `set_org_context`.

**Deferred (disclosed):** free-text-only discount detection (agent should pass structured `discount_minor`); other tools' action families as more consequential tools are wired. **This completes the tiering half of grounded drafts** — the remaining half is the executor→composer wiring (use the seeded prompt layers instead of the skeleton).

**Next recommended action:** founder review + approve commit/merge/push; then the executor→composer wiring.

---

## 2026-08-05 — Executor→composer wiring: prompt activation pipeline (grounded drafts)

**Branch:** `feature/executor-composer-wiring` (off main). **Commit:** merge `af93f06` (pushed to `origin/main`). **No migration, no dependency.**

**Objective:** a routed run composes a real **grounded** prompt (base+vertical+tenant layers) instead of the MVP-055 skeleton — the remaining half of grounded drafts. Discovery: the composer (MVP-059) had no `prompt_bindings` to render (0 rows), base layers were unseeded, and the executor still used the skeleton. Built the **full activation pipeline** (founder-approved).

**`core/prompts/base_layers.py` (new).** `ensure_base_layer(session, archetype)` — idempotently seeds the platform base layer from `prompts/base/<archetype>.md` (global `org_id NULL`); returns None when there's no base file (→ skeleton fallback for that archetype).

**`core/packs/installer.py`.** New step `_activate_prompts` (after `bindings_instances`): per concierge (instance, task) → ensure base layer → `generate_tenant_layer` (from settings) → find the pack vertical layer by the binding's `prompt_layer.ref` anchor (binding task `catalog_answer` → vertical anchor `catalog`) → `pin_binding`. Missing base/vertical or `IncompatiblePin` → skip that task (install never fails on activation).

**`core/runtime/graph.py` + `core/runtime/executor.py`.** `Deps.compose` (a `(state)→(text, content_hash)` callable); `compose_node` uses it (else the skeleton). The executor injects `_make_compose(org, instance, persona)` — resolves the run's task → `get_active_binding` → `composer.render`, **skeleton fallback** when no binding or on any error (composition never blocks a run). `start_run` computes `composed_prompt_hash` via the composer; wired into `start_run`/`resume_run`/`resume_after_approval`.

**`prompts/base/concierge.md`.** Version 1.0 → 1.4 (matches the vertical's `>= 1.4`).

**Requirement → evidence:**
| Behaviour | Test | Result |
|---|---|---|
| install pins base+vertical+tenant bindings for all 4 concierge tasks | `test_prompt_activation::test_install_pins_concierge_prompt_bindings` | PASS |
| archetype with no base layer skipped (no binding) | `::test_archetype_without_base_layer_is_skipped` | PASS |
| executor composes a grounded (non-skeleton) prompt, deterministic hash | `::test_executor_composes_grounded_prompt` | PASS |
| missing binding → skeleton fallback (never blocks a run) | `::test_compose_falls_back_to_skeleton_without_binding` | PASS |

**Commands:** `ruff check .` (pass) · `mypy core` (102, pass) · guards (runtime-not-tools clean — `core.prompts` is not a banned import) · `pytest -q` **553 passed, 0 skipped** (+4) · live smoke (install → 4 concierge bindings + 1 base + 4 tenant layers; a concierge run composes `# base.concierge v1.4 … Identity & safety …` — the real layered prompt).

**Security:** composition is fail-open to the *skeleton* (never blocks or degrades safety — the skeleton still carries the base safety rules once activated); base layers are global platform config; tenant layers are org-scoped (generated, RLS). No external action.

**Deferred (disclosed):** base layers for **nurture/campaigner/ops/support** (only `concierge` authored — others use the skeleton until written); a **re-activation** path when settings change (tenant layer is generated at install; regenerating on settings change is a follow-up — `generate_tenant_layer` is idempotent on content); the `registry._satisfies` `">= "` space-parse quirk (compat currently lenient; latent, not fixed here).

**Next recommended action:** founder review + approve commit/merge/push. **This completes grounded drafts** end-to-end for the concierge: inbound message → routed run (056) → grounded composed prompt (this) → catalog-grounded reply → tiered approval (044 + #20) → audit.

---

## 2026-08-05 — #2 · Close the send loop (an approved/auto reply actually goes out)

**Branch:** `feature/close-send-loop` (off main). **Commit:** merge `91faa96` (pushed to `origin/main`). **No migration, no dependency.**

**Objective:** complete the customer-inquiry chain — a grounded concierge reply is actually sent (simulated Meta) and recorded, tier-gated (objective step 8).

**`core/mediation/tools.py`.** `_messages_send` (was a stub raising `approval_required`) now runs the gated send path: it resolves the conversation (from params or the run), mints the send authorization (audit capability + single-use execution token) **in the proxy's session**, calls `send()`, and returns a structured result. A `SendRefused` (e.g. an unledgered figure) → `{"sent": False, "refused": <code>}` (never raises / trips the breaker).

**`core/channels/whatsapp/send.py`.** `send()` gained an optional `session` param (+ a `_send_session` helper): with a caller session it runs the gates + queued row + outcome in that one transaction (so it can be called from inside the mediation proxy, which already holds the per-org advisory lock — a second `org_scoped_session` would deadlock); standalone callers keep the two-phase self-committing behaviour. Backward-compatible (the normalizer + existing send tests unchanged).

**`core/runtime/executor.py` + `graph.py`.** `Deps.compose` was added earlier; now the executor routes the reply through `messages.send`: at `RESPOND` (for a conversation-bound run) it calls `deps.execute_tool("messages.send", {body, conversation_id, message_class:"transactional"})`. A **pending** result (tier ≥ 2) parks via `_park_send` (checkpoint before respond → resume re-sends, now approved); tier 1 auto-sends. `_drive`/`start_run`/`resume_run`/`resume_after_approval` thread `conversation_id`; the reject branch sends only the customer-safe close.

**Requirement → evidence:**
| Behaviour | Test | Result |
|---|---|---|
| `messages.send` delivers + records the outbound message | `test_send_loop::test_messages_send_delivers_and_records` | PASS |
| an unledgered figure → structured refusal (nothing sent) | `::test_messages_send_refuses_unledgered_figure` | PASS |
| executor routes the reply through `messages.send` (transactional) | `::test_executor_reply_routes_through_messages_send` | PASS |
| a priced reply parks for approval | `::test_executor_priced_reply_parks_for_approval` | PASS |
| **end-to-end through the real proxy** — plain reply auto-sends (tier 1) | `::test_full_run_auto_sends_through_real_proxy` | PASS |
| **priced reply parks, then sends on approve** | `::test_priced_reply_parks_then_sends_on_approve` | PASS |

**Commands:** `ruff check .` (pass) · `mypy core` (102, pass) · guards (24, runtime-not-tools clean — `core.channels` is imported only by the tool layer, not the runtime) · `pytest -q` **559 passed, 0 skipped** (+6). Existing whatsapp send/stop/normalizer + MVP-069 approval suites stay green.

**Security / side effects:** no real external send — `MetaClient` is simulated until `whatsapp_live_enabled`. Every send still passes the five gates (audit capability, execution token, suppression, consent, figure-ledger). Per-org via the proxy's tenant session (RLS). The send authorization is single-use + ctx-bound.

**Deferred (disclosed):** real Meta send (go-live); the passed-session path loses "queued-row-durable-before-send" (fine while simulated); model-composed **quote `figure_refs`** plumbing (a quote must pass its ledger refs to `messages.send`; auto-detected figures still fail-safe via Gate 5); deriving `message_class` from the archetype for marketing sends; the instance-manifest **signing at install** (a real installed instance's manifest isn't re-signed yet — MVP-061 `recompile_instance` seam).

**Next recommended action:** founder review + approve commit/merge/push. This **completes the customer-inquiry chain** (objective steps 5–9). Then #4 (imports: API + CSV + **Excel**).

---

## 2026-08-05 — MVP-076 · Imports migration + batch API (the #4 imports foundation)

**Branch:** `feature/mvp-076-imports-batch-api` (off main). **Commit:** merge `9fcee88` (pushed to `origin/main`). **Dependency added:** `python-multipart` (founder-approved).

**Objective:** onboarding uploads photos/CSVs and tracks a batch through the ingestion pipeline. This ticket lays the foundation (migration + create + state machine + SSE); extraction/review/load are 077–080.

**Migration `3c7f4aa8f204` (017 ingestion).** `import_batches` (org-scoped, +RLS: source_kind, state, filename, byte_size, image_count, row_count, storage_ref, stats, error, created_by→users) + `import_rows` (org-scoped, +RLS: batch_id→import_batches, seq, raw/normalized/confidence/flags jsonb, state, loaded_entity_id, UNIQUE(batch_id, seq)). Per the migration-order doc; not in the vault schema (flagged). Round-tripped; roles re-applied.

**`core/ingestion/state.py`.** The batch state machine — `BatchState` (created→extracting→extracted→validating→review→loading→loaded, + failed/cancelled/reverted) + `advance` (legal-only), `can_transition`, `is_terminal`. `failed` is **resumable** (retries the failed stage). Pure.

**`core/ingestion/storage.py`.** In-process blob store for the upload (real object storage at go-live, like media).

**`core/ingestion/service.py`.** `create_batch` (enforce caps → store blob → insert `created` → emit `import.batch_state`), `transition` (load state → `advance` → persist → emit), `list_batches`/`get_batch`/`list_rows`. Caps: ≤500MB / ≤200 images / ≤5k CSV rows (line-count) → `CapExceeded` with a chunking hint (xlsx row-cap at extraction, 078).

**`core/ingestion/api.py` (+ registered in `core/api/main.py`).** `POST /v1/imports` (multipart, `CATALOG_WRITE`) → 201 or 422+hint; `GET /v1/imports`, `/{id}`, `/{id}/rows` (`CATALOG_READ`); **`GET /v1/imports/{id}/stream`** — an SSE relay (`StreamingResponse`, `text/event-stream`) of the batch's `import.batch_state` events off the Redis stream (block ≤2s → delivered well under 2s).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| **state machine transitions legal-only** (exhaustive property) | `test_import_state::test_advance_permits_exactly_the_legal_transitions` | PASS |
| **batch resumable** (failed → retry) | `::test_failed_batch_is_resumable` | PASS |
| **5k-row cap → clean problem with chunking hint** | `test_import_caps::test_csv_over_the_5k_row_cap_raises_with_chunking_hint`; `test_import_api::test_over_cap_upload_returns_422_with_chunking_hint` | PASS |
| **SSE delivers state changes < 2s** (prompt, filtered, terminal-closing) | `test_import_sse::test_sse_streams_matching_batch_until_terminal` | PASS |
| multipart create → batch + first state event; list; 404 | `test_import_api::*`, `test_import_service::test_create_inserts_batch_and_emits_state` | PASS |
| legal transition emits state; illegal rejected | `test_import_service::test_transition_legal_then_rejects_illegal` | PASS |
| `import_batches`/`import_rows` tenant isolation as `app_rw` | `test_imports_rls::test_import_tables_isolated_under_app_rw` (×2) | PASS |

**Commands:** `ruff check .` (pass) · `mypy core` (106 files, pass) · `mypy migrations` (pass) · `alembic up/down` round-trip (pass) · `pytest -q` **576 passed, 0 skipped** (+17), stable over 2 runs.

**Security:** both tables org-scoped (+RLS, fail-closed), isolation tested. Uploads are gated behind `CATALOG_WRITE`; the caps are server-authoritative. No external side effect. `python-multipart` is a pure-Python parser (no runtime services).

**Deferred (per ticket split):** the **extraction** workers (077 photos / 078 CSV+**xlsx via openpyxl**), the **review queue** (079), **load/revert** (080); real object storage for blobs (in-process now); multi-image blob storage/manifest (a 077 concern — 076 stores the concatenated upload for size accounting). xlsx row-cap enforced at extraction, not upload.

**Next recommended action:** founder review + approve commit/merge/push; then MVP-077 (photo extraction) / MVP-078 (CSV+Excel extraction).

## 2026-08-05 — Support-01 · Support tickets (Growth Operator control plane, slice 1)

**Branch:** `feature/support-tickets` (off main). **Commit:** merge `4140917` (pushed to `origin/main`). **No dependency added.**

**Objective:** the founder-directed **Growth Operator dashboard** — a cross-tenant operator app distinct from the store-owner console. Slice 1: a store owner reports an issue from their console; it lands in the founder's operator queue with **priority + severity**; the operator triages/resolves; the owner sees the resolution. Local-first (runs on `localhost`; lifts to cloud later). The model stays **simulated** (real-AI wiring is a separate approved track).

**Migration `ae1b311f9373` (018 support tickets).** `support_tickets` (org-scoped: raised_by→users, subject, description, category, priority `low|normal|high|urgent`, severity `minor|major|critical`, status `open|in_progress|resolved|closed`, resolution_note, resolved_by/at, CHECK constraints) + `platform_admins` (user_id allowlist). **RLS is split by command:** `p_read`/`p_update` carry the fail-closed platform-admin exception (`org_id = app.org_id OR app.platform_admin='on'`); **`p_insert` is org-only** so the operator can read/resolve across tenants but never file into one. Not in the vault schema/order (flagged, DECISIONS). Round-tripped; roles re-applied.

**`core/tenancy/platform_admin.py` (new).** The single cross-tenant path. `is_platform_admin` checks the `platform_admins` allowlist — the **sole authority** for operator access (deliberately not the org-scoped `founder` role). `get_admin_db` verifies the allowlist then sets the transaction-local `app.platform_admin='on'` GUC (401 no token / 403 non-admin); `admin_scoped_session` is the worker/test equivalent.

**`core/support/`.** `service.py` — owner `raise_ticket`/`list_own`/`get_own` (org-scoped session) + operator `list_all`/`get_admin`/`update_ticket` (cross-tenant session; stamps `resolved_at`/`by` on close; writes each operator change to the affected **tenant's audit chain** via `audit.write`). `schemas.py` — Literal-validated Pydantic models; owner `TicketOut` **hides** cross-tenant fields, operator `AdminTicketOut` adds `org_id`/`org_name`/`raised_by`. `api.py` (registered in main.py) — owner `POST/GET /v1/support/tickets` (+`/{id}`); operator `GET /v1/admin/support/tickets` + `PATCH …/{id}` behind `get_admin_db`.

**Dev tooling.** `scripts/grant_platform_admin.py` + `make grant-admin EMAIL=…` (the only way to become an operator).

**Frontend (`web/`).** Real Vite/React on the existing OTP auth: after a non-simulated sign-in, `SupportConsole` renders the owner **"Report an issue"** form + "My tickets", and an **operator queue** (all stores, inline resolve) that self-reveals only when `GET /v1/admin/support/tickets` returns 200. `api.ts` typed client (`raiseTicket`/`listMyTickets`/`adminListTickets`/`adminUpdateTicket`) + react-query.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Owner raises a ticket → 201, operator-triaged defaults (open/normal), no cross-tenant leak | `test_support_api::test_owner_raises_ticket_with_defaults` | PASS |
| Owner isolation — B cannot see A's ticket | `test_support_api::test_owner_isolation`; `test_support_rls::test_owner_scoped_and_fail_closed` | PASS |
| Non-allowlisted caller denied the operator queue (**403**) | `test_support_api::test_operator_queue_forbidden_for_non_admin` | PASS |
| Operator sees **all tenants**, resolves → owner sees resolved → **audit** row | `test_support_api::test_operator_sees_all_tenants_and_resolves`; `test_support_rls::test_platform_admin_flag_reads_across_tenants` | PASS |
| **Admin flag cannot INSERT into a tenant** (split-RLS guard) | `test_support_rls::test_admin_flag_cannot_insert_into_a_tenant` | PASS |
| Owner cannot file into another org (WITH CHECK) | `test_support_rls::test_owner_cannot_insert_into_another_org` | PASS |
| priority/severity/status validated (422 at boundary + pre-DB in service) | `test_support_validation::*`; `test_support_api::test_invalid_severity_is_rejected`, `::test_empty_patch_is_rejected` | PASS |
| Contract — owner view hides tenant fields; routes registered | `test_support_contract::*` | PASS |
| Frontend type-checks/lints/builds | `tsc -b --noEmit` · `oxlint` · `vite build` | PASS |

**Commands:** `ruff check .` (pass) · `mypy core` (112 files, pass) · `mypy migrations` (pass) · `alembic up/down` round-trip (pass) · `pytest -q` **599 passed** (+23) · web `tsc`/`lint`/`build` (pass). Also a live end-to-end smoke (raise → isolation → 403 → cross-tenant queue → resolve → owner-sees-resolved → audit).

**Security:** the cross-tenant operator path is the platform's first RLS escape hatch — gated by the `platform_admins` allowlist, transaction-local flag only (fail-closed by default), read/resolve only (INSERT stays org-only, isolation-proven), and every operator action audited in the tenant's chain. No external side effect. Billing/SSO deliberately excluded.

**Deferred (disclosed):** `support.ticket.raised.v1` outbox event (would break the vault `topics.yaml` drift test — needs a vault addition; queue reads by poll, so not required); operator notifications; the tenant roster/health views + rest of the control plane (next slices); porting both consoles to a real domain with login; wiring the owner console to a real model for arbitrary-message understanding (separate track).

**Next recommended action:** founder review + approve commit/merge/push; run it locally (`uvicorn` + `cd web && npm run dev`, `make grant-admin EMAIL=you@…`); then choose the next control-plane slice (tenant roster/health) or resume #4 imports (MVP-077/078).

## 2026-08-06 — Support tickets: enterprise (Google/Apple-level) security hardening of the operator plane

**Branch:** `feature/support-tickets` (continues). **Commit:** merge `4140917` (pushed to `origin/main`). **No dependency added.** Founder: "top notch (google/apple level)" + "test rigorously every corner case"; knocked out as a TODO list, one at a time.

**Guarantee (locked):** store owners stay strictly org-isolated; the `app.platform_admin` flag opens **exactly one table (`support_tickets`) and nothing else**; owners can't reach or self-grant the admin plane.

**1 · Least-privilege lock (security #1)** — `tests/isolation/test_platform_admin_scope.py`: an exhaustive structural test asserts the `app.platform_admin` exception is referenced by exactly one table's RLS and never in any INSERT `WITH CHECK`; a runtime test proves the flag is inert on another tenant table (`contacts`). Teeth-verified out of band (injecting the exception on `contacts` flips detection → the guard fails; rolled back).

**2 · Immutable admin-plane audit (security #2)** — **migration 019** `platform_access_log` (append-only via REVOKE + a `BEFORE UPDATE OR DELETE` trigger, like `audit_log`/006; no FKs so records survive entity deletion). `platform_admin.log_platform_access` records **every** cross-tenant action; `service.list_all` logs each queue view (`support.queue.viewed`, count+filters) and `service.update_ticket` logs each resolve (`support.ticket.updated`, target org) — **reads audited, not just writes** — separate from the per-tenant audit chains. Append-only is teeth-tested (INSERT ok; UPDATE/DELETE raise).

**3 · Allowlist governance (security #3)** — **migration 020** `platform_admins.expires_at`; `is_platform_admin` now requires `expires_at IS NULL OR > now()` (an expired admin fails closed). `scripts/grant_platform_admin.py --days N` (auto-expiry) + new `scripts/revoke_platform_admin.py` (+ `make revoke-admin`); both write the grant/revoke to `platform_access_log`. `tests/integration/test_platform_admin_governance.py`: expiry semantics (null/future→valid, past→denied), expired admin → **403**, revoke → **403**, scripts set expiry + log.

**4 · Admin plane off by default (security #4)** — `admin_plane_enabled` (default **false**); `require_admin_plane_enabled` is a router-level dependency on `/v1/admin/*` → **404 before auth** when off (existence hidden, even from a valid admin). Tests opt in via `GROWTH_OPERATOR_ADMIN_PLANE_ENABLED=true`; a dedicated test proves a valid admin AND an anonymous caller both get 404 when off.

**Dev convenience** — `otp_dev_fixed_code` (config) makes the OTP a fixed local-dev code (e.g. `000000`): honoured only when `env=='dev'`, `assert_otp_config_safe` refuses to boot outside dev or on a malformed code, never persisted/returned/logged. `tests/unit/test_auth_otp.py` + `test_otp_delivery.py` + a `test_auth_flow.py` end-to-end (signs in with `000000`, code not in the response).

**Deploy-time controls (recorded in DECISIONS, NOT built):** MFA/step-up for operators; separate deployment + network isolation; dual-control for grants; anomaly/rate alerting + denied-attempt logging; PII minimization in the operator view.

**Commands:** `ruff check .` (pass) · `mypy core` (111) + `mypy migrations` (pass) · `alembic up/down` round-trip 018→020 (pass) · `pytest -q` **623 passed** (+24 over the pre-hardening 599). Migrations 019/020 not in the vault schema/order — flagged (BLOCKERS #21).

**Next recommended action:** founder review + approve commit/merge/push for the whole `feature/support-tickets` branch (support tickets + hardening); then next control-plane slice (tenant roster/health) or #4 imports (MVP-077/078).

## 2026-08-06 — Phase 1 · Two-plane RBAC foundation (tenant owner/manager/staff/viewer + platform dev/admin/staff/analyst)

**Branch:** `feature/phase1-rbac`. **Commit:** merge `c338eb4` (pushed to `origin/main`). **No dependency added.** First slice of the multi-plane program (separate apps + full role matrix + ROI-now — founder-approved 2026-08-06); the identity foundation every dashboard downstream gates on. The design mirrors how AWS/GCP/Stripe separate the control plane from the data plane (DECISIONS 2026-08-06). Built as two tested tickets.

**Ticket 1.1 — Tenant RBAC.** Retired the tenant **`founder` role + `platform:admin` permission** (a tenant role granting a platform permission was a latent cross-tenant escalation — verified zero `founder` memberships first). `core/tenancy/permissions.py`: roles `owner/manager/staff/viewer` + the full permission grid (added `conversations:*`, `customers:*`, `campaigns:read`, `insights:read`, `members:manage`, `billing:manage` — defined ahead of their features, deny-by-default) + `ROLE_RANK`/`can_grant_role`/`assignable_roles`. **Migration 021**: widened the `user_orgs` + `invites` role CHECK (drops `founder`), reseeded the drift-tested RBAC catalog to mirror the code; guards fail closed if a `founder` membership exists. **Invites carry a role**, rank-gated (`can't grant above your own level`). Cleaned up the retire-`platform:admin` fallout: `api_keys` (issue key) + `ops` (run viewer) were gated on `requires(PLATFORM_ADMIN)` but both act on the caller's OWN org → re-gated to tenant `org:manage`.

**Ticket 1.2 — Platform RBAC.** `core/tenancy/platform_permissions.py`: a **separate** namespace (`platform.*`) with roles `dev/admin/staff/analyst` + `PLATFORM_ROLE_PERMISSIONS` (dev=all incl. impersonate/debug; admin=tenants/operators/tickets/insights; staff=tickets+read; analyst=read-only). **Migration 022**: `platform_admins.role` (default `admin`, CHECK). `platform_admin.resolve_platform_role` + **`require_platform(perm)`** (verify allowlist → resolve role honoring expiry → check permission → set the audited cross-tenant flag); the support endpoints gate on `platform.tickets:read`/`:resolve`. `grant-admin --role`.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Tenant matrix per role (owner/manager/staff/viewer) | `test_rbac::test_permission_matrix` | PASS |
| Grant hierarchy — can't grant above your own rank | `test_rbac::test_can_grant_role_respects_rank`; `test_invites_flow::test_cannot_invite_a_role_above_your_own` | PASS |
| Invite-with-role end-to-end (member joins as that role) | `test_invites_flow::test_owner_invites_with_role_and_member_joins_as_that_role` | PASS |
| `founder` retired — CHECK rejects it, grants nothing | `test_invites_flow::test_user_orgs_check_rejects_founder_accepts_new_roles`; `test_rbac::test_no_role_denies_everything` | PASS |
| RBAC catalog == code (drift) | `test_rbac_seed::test_seed_matches_constants` | PASS |
| Platform matrix per role (dev/admin/staff/analyst) | `test_platform_rbac::test_platform_permission_matrix` | PASS |
| **Plane separation** — tenant⊥platform permission namespaces | `test_platform_rbac::test_permission_namespaces_are_disjoint` (+ leak tests both directions) | PASS |
| Per-role operator-endpoint gating (analyst 403, others 200) | `test_platform_admin_governance::test_operator_queue_gated_by_platform_role` | PASS |
| Migrations 021/022 up/down | `alembic` round-trip | PASS |

**Commands:** `ruff check .` (pass) · `mypy core` (112) + `mypy migrations` (pass) · `alembic up/down` round-trip 018→022 (pass) · `pytest -q` **673 passed** (+50 over Phase-0's 623) + a live per-role smoke (dev/admin/staff see cross-tenant tickets; analyst 403; tenant-owner token 403).

**Security:** the two planes' permission namespaces are **provably disjoint** (test); cross-tenant power lives only in the platform plane; the retired-`founder` footgun is closed AND the mislabeled endpoints re-homed. Tenant isolation + the platform-admin hardening (least-privilege lock, immutable access log, expiry/revoke, off-by-default) all unchanged and green.

**Deferred (disclosed):** a **member role-change** endpoint (invite-with-role covers new members; changing an existing member's role is the Phase-3 members-management UI); migrations 021/022 not in the vault schema/order (BLOCKERS #21); the new tenant permissions (conversations/customers/campaigns:read/insights/billing) are unused until their features ship (Phase 3+).

**Next recommended action:** founder review + commit/merge/push `feature/phase1-rbac`; then Phase 2 (two apps + logins).

## 2026-08-06 — Phase 2 · Two apps + logins (separate customer + operator front-ends)

**Branch:** `feature/phase2-apps`. **Commit:** merge `804e889` (pushed to `origin/main`). **Dependency added:** `vitest` (dev-only, both web apps; founder-approved). The single `web/` app is split into two independently-deployable apps that share the FastAPI backend but **no front-end code** — the operator app never ships to a store (the Phase-1 "separate deployment" decision, realised). Built as three tested tickets.

**Ticket 2.1 — `GET /v1/admin/me`.** `core/tenancy/platform_router.py` (new): the operator app's identity check — returns `{user_id, role, permissions}` for a valid operator. Behind the admin-plane gate (404 when off) + `require_platform()` (403 non-operator, 401 no token). Moved `require_admin_plane_enabled` from `core/support/api.py` to `core/tenancy/platform_admin.py` (shared by both `/v1/admin/*` routers). Backend at **680 pytest** (+7: per-role identity + permission sets, 403/401/404 gates).

**Ticket 2.2 — Customer app (`web/`).** react-router + a **role-aware shell** (nav gated by tenant role from `/v1/me` — Team appears only for owner/manager); a real auth context (token in `localStorage`, `/v1/me` hydration, sign-out, stale-token self-clear); the owner **support** screens (report + my tickets) moved in; the operator queue **removed**; a **Team** section using the Phase-1 **invite-with-role** (the role picker only offers roles at/below your own). Dropped the demo `simulate` code. Added `scripts/dev_make_owner.py` + `make make-owner` so a local email becomes a store owner (to demo the shell).

**Ticket 2.3 — Operator app (`web-ops/`).** A brand-new Vite/React/Tailwind app (own package/build, port 5174, dark "internal console" theme). Separate login + token key → gated on `/v1/admin/me`, cleanly distinguishing operator / not-an-operator (403) / plane-off (404) / backend-down. **Role-aware operator nav** gated by the *permissions* `/v1/admin/me` returns (dev→queue+stores+debug; admin/staff→queue+stores; analyst→stores only). The cross-tenant **support queue** (list + inline resolve, resolve gated on `platform.tickets:resolve`) moved here; Stores/Debug are Phase-4/dev placeholders.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Operator identity endpoint (role+perms; 403/401/404 gates) | `test_platform_me` (7) | PASS |
| Customer role-aware nav (owner/manager see Team; staff/viewer don't) | `web` vitest `roles.test` | PASS |
| Customer invite rank-gating (can't grant above own) | `web` vitest `assignableRoles` | PASS |
| Operator role-aware nav (analyst no queue; dev sees debug) | `web-ops` vitest `roles.test` | PASS |
| Customer login → `/v1/me` → owner shell → report ticket | live smoke (make-owner → OTP 000000 → org+owner → 201) | PASS |
| Operator per-role queue access | 2.1 live smoke (dev/admin/staff 200, analyst 403) | PASS |

**Commands:** backend `pytest -q` **680 passed** · `ruff` + `mypy core` (113) clean. `web`: `tsc` clean · `vitest` **10/10** · `oxlint` exit 0 (2 cosmetic HMR warnings) · `vite build` ok. `web-ops`: `tsc` clean · `vitest` **6/6** · `oxlint` exit 0 (2 HMR warnings) · `vite build` ok; both dev servers boot + serve 200 (5173 / 5174).

**Security:** two separate bundles → **no operator code in the customer app**. Operator app gated end-to-end (allowlist + admin-plane flag + `/v1/admin/me`), off-by-default. Tokens in `localStorage` (separate keys per app). Front-end gating is UX only; the backend enforces every call. No new backend surface beyond the read-only `/v1/admin/me`.

**Deferred (disclosed):** store onboarding (create-your-store) flow — the customer app shows a "no store" state; `make make-owner` is the local stand-in. The two front-ends are **not in CI** yet (add a `web`/`web-ops` build job — ops follow-up). The 4 HMR lint warnings (hook+component in one file) are cosmetic. Operator Stores/Debug are placeholders (Phase 4).

**Next recommended action:** founder review + commit/merge/push `feature/phase2-apps`; then Phase 3 (customer dashboards).

---

## 2026-08-07 — Phase 3 (Tickets 3.1–3.3): Customer dashboard — Home+shell, Approvals queue, Conversations & leads

**Branch:** `feature/phase3-dashboards` (off main). **Commit:** `8930be8`; **merged to main** `9a6ebb5`. **No migration, no dependency.**

**Approved plan:** build the store-owner dashboard on **real data**, operational sections only. Per the founder's 2026-08-06 direction (DECISIONS): the CEO-grade analytics/math lives in the operator console (Phase 4); the owner gets distilled **outcomes** + drill-down + an ask-GO thread once the analytics engine (Phase 3.5) lands. "Almost-production" bar: real endpoints, typed models, RLS + RBAC + isolation-tested; polished role-gated UI with loading/empty/error states; unit-tested nav/logic.

**Ticket 3.1 — Home + shell.** `core/insights/service.py` + `api.py`: `GET /v1/dashboard/overview` — one round-trip of four **org-scoped** counts (pending approvals / open conversations / active catalog items / open tickets), RLS via `set_org_context` **and** explicit `org_id` filter, gated `insights:read`. `web/`: expanded to the full role-gated **8-section** shell (Home · Approvals · Conversations · Catalog · Customers · Support · Team · Settings) with a **permission-based** nav (`lib/roles.ts` mirrors `core/tenancy/permissions.py`), a Home with KPI tiles (loading skeleton / error / empty) + tasteful `ComingSoon` placeholders (each a one-file swap for its later ticket); title fix.

**Ticket 3.2 — Approvals queue (HITL core).** `core/approvals`: `list_approvals` now returns `matched_rules`; the list endpoint got a typed `ApprovalSummary` (was untyped `list[dict]`, unconsumed). `ApprovalsSection` renders each parked draft (friendly title — reply vs **quote** when priced — body, price, tier badge, the "why" chips, expiry) → **approve / reject(+reason) / edit-then-approve**; resolve is unchanged, so the approve-with-edit **tier-raise guard** still holds. Actions gated by `approvals:resolve` (viewer sees the queue read-only). `lib/approvals.ts` label/draft/price/expiry helpers.

**Ticket 3.3 — Conversations & leads.** New read-only module `core/conversations/` (`service.py` + `api.py`; precedent: `core/support`): `GET /v1/conversations` (inbox — contact + last-message preview + count via a LATERAL subquery), `GET /v1/conversations/{id}` (thread — messages ascending; 404 for a cross-org id, so no resource-existence leak), `GET /v1/leads` (pipeline). All RLS + explicit-org-scoped, gated `conversations:read`; `intent` (jsonb, uncertain shape) deliberately not exposed. `ConversationsSection` = **Inbox** (responsive master-detail list↔thread; inbound/outbound bubbles) + **Pipeline** (leads grouped into the 6 stage columns). `lib/leads.ts` (stages/`groupByStage`) + `lib/conversations.ts` (direction/preview).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Overview: 4 org-scoped KPIs, status-filtered, empty→0, 401/400/403 | `test_dashboard_overview` (6) | PASS |
| Approvals: list+`matched_rules`, org-scoped, approve/reject, 410/404, 403 | `test_approvals_api` (7) | PASS |
| Approve-with-edit tier-raise still rejected | `test_approval_service` (existing) | PASS |
| Conversations: inbox+last-msg, thread ascending, 404 cross-org, leads, 403 | `test_conversations_api` (6) | PASS |
| Role-gated nav (8 sections) + perm map + Home tiles + lead grouping | `web` vitest (roles 14, home 2, approvals 6, leads 3, conversations 2 = 27) | PASS |

**Commands:** backend `ruff check .` clean · `mypy core` **118** clean · `pytest` (3 new API suites + `test_scaffold` import-clean) **21 passed**; `web` `tsc -b` clean · `vitest` **27/27** · `oxlint` exit 0 (2 pre-existing HMR warnings) · `vite build` ok; real-HTTP uvicorn smokes (overview 401-gated + boots; approvals list→approve→pending 0). Both dev servers serve 200.

**Security:** all new endpoints RLS-scoped + explicit `org_id` filter + isolation-tested (org A never sees org B); read-only except the pre-existing resolve; frontend gating is UX only — every call enforces its permission + org context server-side; store's own customer PII (phone/message bodies) shown only to its `conversations:read` members. Also hardened all three new test fixtures to **unique per-fixture emails** (a shared-email literal leaked on a mid-setup error; cleaned 12 orphan test orgs).

**Known issues / deferred:** the layered outcome cards + ROI + campaigns arrive with the analytics engine (Phase 3.5); Catalog/Customers/Settings are placeholders (**3.4–3.6**, next); `core/conversations` is a new module not in the vault module map (additive, flagged — precedent `core/support`); front-ends still not in CI (ops follow-up).

**Next recommended action:** founder review; continue to Ticket 3.4 (Catalog & pricing). Merge/push `feature/phase3-dashboards` per founder instruction (then record the merge hash here + in MVP_STATUS, replacing `pending`).

---

## 2026-08-07 — Phase 3 (Tickets 3.4–3.5): Catalog & pricing, Customers/CRM

**Branch:** `feature/phase3-dashboards` (continues after `8930be8`). **Commit:** `b32338d`; **merged to main** `9a6ebb5`. **No migration, no dependency.**

**Ticket 3.4 — Catalog & pricing (frontend-only).** The catalog backend (list w/ cursor, detail, hybrid search, create/patch/delete with ETag + idempotency) already exists and is tested, so **no backend change**. `web/`: `CatalogSection` — searchable item grid; each card shows title, SKU, **price** (static → ₹ `base_price_minor`, computed → **"Live rate"** badge), availability badge, and a few pack attributes (rendered generically — no jewelry nouns hardcoded); **create / edit / archive** inline, gated `catalog:write` (staff/viewer read-only), rupees↔minor conversion; the backend's structured 422 (attribute validation) now reads legibly via a small `authed` improvement. Pricing conveyed per-item rather than coupling to `rates/status` (per-source freshness of uncertain shape, needs a pack). `lib/catalog.ts` (priceLabel / availabilityLabel / rupeesToMinor).

**Ticket 3.5 — Customers / CRM.** New read-only module `core/customers/` (precedent: `core/support`, `core/conversations`): `GET /v1/customers` (contacts + **lead + order counts** via subqueries) and `GET /v1/customers/{id}` (profile + `language_pref`/consent/attributes + their **leads**, **conversations**, and **orders/purchase history**; 404 for a cross-org id). All RLS + explicit-org-scoped, gated `customers:read`. `CustomersSection` = responsive **master-detail**: list ↔ detail (profile + consent badge, preferences, orders with running total, pipeline stages reusing the leads styling, conversations). `lib/customers.ts` (consentLabel / orderStatusLabel / money).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Catalog list/search render (price + availability); create/edit gated `catalog:write` | `web` vitest `catalog.test` (4) + existing `test_catalog_crud`/`_search` (11) | PASS |
| Customers list + lead/order counts, org-scoped | `test_customers_api::test_list_customers_with_counts` / `_org_scoped` | PASS |
| Customer detail = profile + leads + conversations + orders | `test_customers_api::test_customer_detail_has_history` | PASS |
| Cross-org customer detail → 404 | `test_customers_api::test_customer_detail_cross_org_is_404` | PASS |
| CRM gated by `customers:read` | `test_customers_api::test_customers_forbidden_without_permission` | PASS |

**Commands:** backend `ruff check .` clean · `mypy core` **121** clean · `pytest test_customers_api` **5 passed** + `test_catalog_crud`/`_search` **11 passed** (regression); `web` `tsc` clean · `vitest` **34/34** (+catalog 4, +customers 3) · `oxlint` exit 0 (2 pre-existing warnings) · `vite build` ok; real-HTTP uvicorn smokes (catalog list/search shapes + 403; customers list `[]` / unknown 404 / no-roles 403).

**Security:** the new `core/customers` endpoints are RLS + explicit-org-scoped + isolation-tested (org A never sees org B); read-only; store's own customer PII (phone/email/orders) shown only to its `customers:read` members. Catalog write path unchanged (`catalog:write`, server-enforced). Frontend gating is UX only.

**Known issues / deferred:** quotes + appointments (link via `lead_id`, not `contact_id`) not yet in the CRM detail; catalog `PATCH` sent without `If-Match` (optimistic concurrency skipped — acceptable for a single-owner dashboard, backend tolerates it); the **CRM depth question** (Zoho/HubSpot-level segmentation/automation/analytics) raised by the founder for post-merge discussion. Settings + autonomy is **3.6** (next). `core/customers` is a new module not in the vault map (additive, flagged; precedent `core/support`/`core/conversations`).

**Next recommended action:** commit 3.4–3.5, merge `feature/phase3-dashboards` to main, push; record the merge hash here + in MVP_STATUS (replace `pending`); then discuss CRM depth + plan Ticket 3.6.

---

## 2026-08-07 — Phase 3 Ticket 3.6: Settings & the autonomy volume-knob (Option A — wired live)

**Branch:** `feature/phase3-6-settings` (off main). **Commit:** `d60cc8f`; **merged to main** `48f4117`. **No migration, no dependency.** **Completes Phase 3 (customer dashboard, 3.1–3.6).**

**Approved plan (Option A, founder 2026-08-07):** the knob must genuinely change behaviour, not write an inert setting. Discovery: `autonomy.*` settings existed but nothing read them; the real gate is the approval-engine tiers. So the knob is wired into the live decision, floored by the immovable tier-4 money set. Full design + safety proof recorded in DECISIONS 2026-08-07.

**Backend.** `core/tenancy/settings.py`: autonomy keys **free-dial** (`tighten_only=False`, superseding the old rule); default **`auto`** (so wiring changes nothing until an owner tightens); added `autonomy.campaigns` + `autonomy.paused`. `core/approvals/engine.py`: `_autonomy_floor` + a **max-tier overlay** in `evaluate_tool` — `auto` respects pack tiers; else / paused forces `AUTONOMY_REVIEW_TIER (2)`. Because `max()` wins, it can only raise a tier → the `CORE_TIER4_ACTIONS` floor is immovable by construction. `core/tenancy/settings_router.py`: `GET /v1/settings/autonomy` (levels + pause + fixed floor), owner-gated (`org:manage`). The **settings-change audit already exists** (`write_setting` records `settings.changed` with an old→new diff — surfaced, not rebuilt).

**Frontend (`web/`).** `SettingsSection.tsx`: store profile · **Pause-all-autonomy** switch · per-capability **Auto/Review** controls (Messaging/Pricing/Campaigns) · a locked **"always needs your approval"** floor panel · Preferences (reply tone, quiet hours). `lib/settings.ts` (+test).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| `auto` respects pack tiers (no-op) | `test_autonomy_gate::test_auto_default_is_noop` | PASS |
| Review / Off / Pause force approval | `test_review…` / `test_off…` / `test_pause_forces_approval_globally` | PASS |
| Priced reply picks up pricing capability | `test_priced_reply_uses_pricing_capability` | PASS |
| **Tier-4 money floor immovable at every knob position** | `test_tier4_floor_immovable_at_every_knob_position` | PASS |
| Free-dial loosening allowed (no 409) | `test_free_dial_loosening_is_allowed` + real-HTTP | PASS |
| Owner-only read/write | real-HTTP smoke (staff 403) | PASS |
| Knob UI + floor labels | `web` vitest `settings.test` | PASS |

**Commands:** `ruff check .` clean · `mypy core` **121** · **`scripts/guards` 0 violations** · **full `pytest tests/unit` 351 passed** · `test_autonomy_gate`+`test_settings`+no-regression (bridge/send-loop/engine) **30 passed**; `web` `tsc`/`vitest` **37**/`oxlint`(2 pre-existing)/`build` ok; real-HTTP smoke (defaults auto → free-dial write → pause+level persist → staff 403). **Ran the full CI-equivalent locally before push** (the 3.4 lesson).

**Security:** the knob can only *tighten* autonomy (raise a tier); the tier-4 money floor is immovable and tested at every position; owner-only (`org:manage`); every change audited. Frontend gating is UX only.

**Known issues / deferred:** the finer ladder (`suggest`/`off` vs `draft_only`, true-disable) collapses to "force approval" at the binary gate — deeper granularity + all other autonomy depth in `PRODUCTION_DEPTH_BACKLOG.md`; the `autonomy.paused`/`*` resolves add a couple of DB reads per gate eval (fine for MVP; batch later).

**Next recommended action:** commit 3.6, merge `feature/phase3-6-settings` to main, push, verify CI green, record the merge hash. Then **plan Phase 3.5-eng (analytics & intelligence engine)** before building; then Phase 4.

---

## 2026-08-07 — Phase 3.5-eng A1 + A2.1: analytics rollup foundation + campaigns model

**Branch:** `feature/phase35-eng-analytics` (off main). **Commit:** `aa30edf`; **merged to main** `ab1aed0`. **No dependency.** Founder-approved phase plan (~8 tickets A1→A4.3); this batch is the first two, committing at a phase checkpoint.

**A1 — event-facts + rollup foundation.** **Migration 023** `business_metrics` (org-scoped +RLS): `(org_id, metric_date, metric_key, dimension, value_numeric, value_minor)` + UNIQUE for idempotent upsert. `core/insights/metrics.py`: `compute_day` (counts leads/quotes/orders/revenue/messages_in/out from the domain tables), `upsert_day` (idempotent), `weekly_summary` (this-week vs last-week + WoW %). `core/insights/rollup.py`: `rollup_org` + the scheduled `business_metrics_rollup` job (daily 00:15 UTC, fans out per org via `org_scoped_session`, recomputes the trailing 30 days) — registered in `core/scheduler.py` (scheduler job-set test updated). `GET /v1/insights/summary` (`insights:read`). Owner surfacing: Home's placeholder → a real **"This week"** outcome card (New inquiries · Quotes · Orders · Revenue, each with a ↑/↓ WoW delta; empty state until a store has data). `lib/insights.ts` (+test).

**A2.1 — campaigns model + persistence.** **Migration 024** `campaigns` (org-scoped +RLS; lands at 024, not the doc's 018 slot — flagged, precedent `incidents`). `core/campaigns/`: `service` (create/list/get + `record_execution`), `consumer` (`@consumer(campaign.executed.v1)` → records send/failed counts, org-scoped, idempotent; wired in `core/worker.py`), `api` (`POST /v1/campaigns` `campaigns:send`; `GET` list/`{id}` `campaigns:read`). **Honest finding (flagged):** `campaign.*` events are *defined* (topics.yaml/types.py) but **emitted by nothing** — there is no campaign send-lifecycle yet (the campaigner agent's execution is future work). So the consumer is wired + ready and idle; `create` makes a real record now so the model + (A2.2) analytics have data. Backend-only (campaign analytics is operator-side, Phase 4).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Rollup counts domain tables; idempotent; org-scoped | `test_business_metrics` (compute/idempotent/scoped) | PASS |
| Week-over-week summary + delta + `insights:read` gate | `test_weekly_summary_wow_delta` / `test_summary_endpoint` / `_requires_permission` | PASS |
| Campaign create/list/get, org-scoped, gated | `test_campaigns` (create/list/404/send-gate/read-gate) | PASS |
| `campaign.executed` consumer records send counts | `test_campaigns::test_consumer_records_execution` | PASS |

**Commands:** `ruff check .` clean · `mypy core` **127** · **`scripts/guards` 0** · **full `pytest tests/unit` 358** (+scheduler job-set updated, +campaigns import-clean) · A1 **6** + A2.1 **7** pytest; migrations 023/024 up/down round-trip + RLS forced (2 policies each); `web` `tsc`/`vitest` **41** (+insights 4)/`oxlint`(2 pre-existing)/`build`; real-HTTP smokes (summary WoW; campaign create→list→consumer→executed). **Full CI-equivalent run locally before push** (the 3.4 lesson).

**Security:** both new tables RLS + org-scoped + isolation-tested; the consumer opens one `org_scoped_session` per event (never cross-tenant). Read/write gated (`insights:read`, `campaigns:read`/`:send`). No external side effect (consumer only records; no real send).

**Known issues / deferred:** campaign send-lifecycle (emits `campaign.executed`) is future — consumer idle until then; A2.2 funnel/significance needs real campaign traffic to be meaningful (correct-but-sparse). Migrations 023/024 not in the vault (additive, flagged).

**Next recommended action:** commit A1+A2.1, merge to main, push, verify CI green, record the merge hash. Then A2.2 (funnel + significance + drop-off).

---

## 2026-08-08 — Phase 3.5-eng A2.2+A3.1 + A3.2 + A4.1: campaign analytics engine + insight-record framework

**Branch:** `feature/phase35-eng-a22-attribution` (off main). **Commit:** `0970212`; **merged to main** `52f9776`. **No dependency.** Full design + founder rationale in DECISIONS 2026-08-08. Committed as one CI-cleanable unit at a phase checkpoint (founder: "commit to main CI cleanable unit").

**A2.2 + A3.1 (combined — founder wanted exact attribution AND the "why" together).** **Migration 025** `campaign_touches` (+RLS). `core/campaigns/attribution.py`: **exact deterministic first-touch** attribution (a conversion is credited to the campaign that FIRST touched the contact within the window) → `campaign_funnel` (reached→leads→quotes→sales + revenue) + `org_baseline_rate`. `core/campaigns/analytics.py` (pure): one-sample proportion **z-test** (real lift vs noise), funnel conversion rates, **drop-off** diagnosis, verdict headline. `GET /v1/campaigns/{id}/analytics`. Multi-touch + `campaign_metrics` rollup deferred (backlog).

**A3.2 (ROI + drivers).** **Unhackable ROI** — revenue only from immutable `orders.total_minor` (no injectable figure), cost = real `sent_count` × the owner's `campaign.cost_per_message_minor` setting, org-isolated, deterministic; cost 0 → ROI undefined. Plain-language **drivers** (`analytics.drivers` — Reach/Conversion/Bottleneck/ROI, each with a note + good/bad/neutral). Wired into `campaign_analytics` + `/analytics`. No migration.

**A4.1 (agent-report framework).** **Migration 026** `agent_reports` (+RLS) — the layered insight record **verdict → drivers → full_breakdown → evidence** (jsonb) + report_type / subject_ref / confidence / model. `core/insights/reports.py` + `GET /v1/insights/reports(/{id})` (`insights:read`). The shape A4.2 (campaign-analysis producer) + A4.4 (simulated agents) write into.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| z-test / funnel / drop-off / verdict; ROI / drivers | `test_campaign_analytics` (12 unit) | PASS |
| Exact first-touch (routing/window/untouched) + endpoint | `test_campaign_attribution` (4) | PASS |
| Layered insight record round-trips + list/detail/isolation/gate | `test_agent_reports` (6) | PASS |

**Commands:** `ruff check .` clean · `mypy core` **130** · **guards 0** · **full `pytest tests/unit` + campaign + attribution + reports** — **369 tests** across the touched suites; migrations 025/026 up/down round-trip + RLS forced. Full CI-equivalent run locally before push.

**Security:** revenue/attribution/ROI are deterministic + **strictly org-isolated** (RLS + explicit filter) — a store can never pull another store's orders; no user-supplied figures enter ROI. All three new tables RLS-forced. No external side effect (no LLM; producers are A4.4, gated-simulated).

**Known issues / deferred (backlog):** multi-touch attribution, configurable window/rule, `campaign_metrics` rollup, confidence intervals; the "replied" funnel stage; the campaign send-flow that populates `campaign_touches` (future). Migrations 025/026 not in the vault (flagged).

**Next recommended action:** commit + merge to main + push + verify CI + record hash. Then A4.2 (campaign-analysis producer → stored insight record).

---

## 2026-08-08 — Phase 3.5-eng A4.2 + A4.3 + A4.4: intelligence producers (campaign analysis, tracked competitors, simulated agents)

**Branch:** `feature/phase35-eng-a42-producer` (off main). **Commit:** `d71f99c`; **merged to main** `ffead49`. **No dependency.** One CI-cleanable unit; full rationale in DECISIONS 2026-08-08.

**A4.2 — campaign-analysis producer (deterministic, no LLM).** `core/campaigns/producer.py` runs the A2/A3 engine (`campaign_analytics`) and stores a layered `agent_report` (report_type=`campaign_analysis`, subject=campaign, `model="deterministic"`): verdict + drivers + full_breakdown (funnel/significance/ROI/drop-off). `analytics.verdict_line` (pure owner one-liner). `POST /v1/campaigns/{id}/report` (`campaigns:read`).

**A4.3 — tracked competitors.** **Migration 027** `tracked_competitors` (+RLS). `core/competitors/` service (`DELETE … RETURNING`) + api: `POST`/`DELETE` (`campaigns:send`, owner/manager), `GET` list/`{id}` (`insights:read`, all roles). The input to A4.4.

**A4.4 — simulated competitor + marketing agents.** `core/insights/agents.py`: a **competitor-analysis** producer (over `tracked_competitors`) and a **marketing-strategist** producer (heuristics over the A1 weekly metrics), both **gated-simulated** — off (`llm_provider_enabled`=false, default) → deterministic clearly-labelled output (`model="simulated"`); on-but-unwired → **fail closed** (`provider_unavailable`, RealModel posture). `POST /v1/insights/reports/generate {report_type}` (`campaigns:send`).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Campaign producer stores a layered report; endpoint persists; verdict_line | `test_campaign_producer` (2) + `verdict_line` unit | PASS |
| Competitors CRUD, org-scoped, RBAC split (staff view-only) | `test_competitors` (4) | PASS |
| Competitor/marketing producers write simulated insights; gate fails closed; endpoint gate | `test_insight_agents` (4) | PASS |

**Commands:** `ruff check .` clean · `mypy core` **135** · **guards 0** (no industry nouns in the simulated output) · **full `pytest tests/unit`** + all four campaign/competitor/agent suites — **374 tests** across the touched suites; migration 027 up/down round-trip + RLS forced. Full CI-equivalent run locally before push.

**Security:** all reads/writes org-scoped + RLS + isolation-tested; no LLM/network (agents gated-simulated, tests never hit a provider); competitor names live only in tests, not `core/` (guards clean). No external side effect.

**Known issues / deferred (backlog):** the competitor agent's body is a placeholder over the tracked list (real web/LLM research is future); real LLM wiring is a gated go-live step. A4.5 (owner⇄GO thread) + A4.6 (owner Insights UI) remain.

**Next recommended action:** commit + merge to main + push + verify CI + record hash. Then A4.5 (owner⇄GO cross-tenant thread).

---

## 2026-08-08 — Phase 3.5-eng A4.5: owner⇄GO thread (the cross-tenant one)

**Branch:** `feature/phase35-eng-a45-thread` (off main). **Merge:** `8e4e1e1`. **No dependency.** Full security rationale in DECISIONS 2026-08-08.

**Migration 028** `insight_messages` (+RLS) — the owner⇄Growth-Operator Q&A on an insight, with **split-RLS** carrying a scoped operator INSERT: `p_read`= own-org OR platform-admin; `p_insert`= `(own-org AND author_type='owner') OR (platform-admin AND author_type='operator')`. Append-only; a `resolve_report_org` SECURITY DEFINER helper finds a report's org. `core/insights/thread.py` (owner ask/read org-scoped; operator reply on the admin session). Owner `GET`/`POST /v1/insights/reports/{id}/messages` (`insights:read`); operator `POST /v1/admin/insights/reports/{id}/reply` (`require_platform` + admin-plane gate, audited to `platform_access_log`).

**Security (the important part): the least-privilege lock (isolation #1) was UPDATED to a tighter invariant, not loosened.** The `app.platform_admin` blast radius is now exactly `{support_tickets, insight_messages}` (each with isolation tests); the flag may appear in an INSERT WITH CHECK **only on `insight_messages`, only scoped by `author_type='operator'`** — a structural test enforces both, and a teeth test proves an owner **cannot forge an operator-authored message** (RLS `WITH CHECK` rejects it). A cross-org read is 404; a non-operator is 403 on the reply endpoint.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Owner asks, operator answers cross-tenant, owner sees both | `test_insight_thread::test_owner_asks_operator_answers` | PASS |
| Owner can't read another org's thread | `test_owner_cannot_read_another_orgs_thread` | PASS |
| **Owner can't forge an operator message (RLS teeth)** | `test_owner_cannot_forge_an_operator_message` | PASS |
| Reply forbidden for a non-operator | `test_reply_forbidden_for_non_operator` | PASS |
| Least-privilege lock: flag on exactly 2 tables; INSERT-check scoped | `test_platform_admin_scope` (updated) | PASS |

**Commands:** `ruff check .` clean · `mypy core` **136** · **guards 0** · **full `pytest tests/unit` + `tests/isolation`** — **388 tests**; migration 028 up/down round-trip + RLS forced (p_read/p_insert). Full CI-equivalent run locally before push.

**Next recommended action:** commit + merge to main + push + verify CI + record hash. Then A4.6 (owner Insights UI) — the last analytics/intelligence ticket.

---

## 2026-08-08 — Phase 3.5-eng A4.6: owner Insights UI (engine complete)

**Branch:** `feature/phase35-eng-a46-insights-ui` (off main). **Merge:** `9bbb031`. **Frontend-only —
no backend, no migration, no new dependency.** Consumes the A4.1/A4.5 endpoints already shipped.

**The design decision (founder, 2026-08-08): leveled questions by intensity, not AI auto-replies.**
The owner drills through an insight as four escalating questions, each revealing a deeper layer of the
record — **all real stored data, no AI call, no wait**: (1) *What happened?* → `verdict`; (2) *Why?* →
`drivers` (plain-language, good/bad/neutral); (3) *Show me the numbers* → `full_breakdown` (funnel,
significance, ROI); (4) *Prove it* → `evidence`. Below that, a free-text **Ask Growth Operator** thread
backs anything the four levels don't cover — answered by a **human operator** (the A4.5 thread), with
honest "Growth Operator will reply here" microcopy. **No fabricated AI answer anywhere.** When the LLM
provider is wired later it slots in behind the same UI as an operator drafting aid — the interface
doesn't change.

**Files (all `web/`):** `api.ts` (+`InsightReport*`/`ThreadMessage` types + 4 fetchers);
`lib/insights.ts` (+`QUESTION_LEVELS`, `reportTypeLabel`, `driverTone`, `confidenceTone`,
`humanizeBreakdownKey`, `formatBreakdownValue` — all pure); `lib/insights.test.ts` (+14 cases);
`components/InsightsSection.tsx` (**new** — list → leveled drill-down → thread; generic breakdown
renderer works across all three report types); `router.tsx` (+`/insights`); `components/Shell.tsx`
(+nav link gated `insights:read`); `lib/roles.ts` + `roles.test.ts` (Insights added to NAV — a shared
read section for staff/viewer; pinned nav test updated, not weakened).

**Requirement → evidence:**
| Criterion | Evidence | Result |
|---|---|---|
| Owner sees the verdict headlines (list) | `getInsightReports` + `ReportCard`; `insights:read` gated | PASS |
| Leveled drill-down reveals each record layer | `QUESTION_LEVELS` + `LayerBody`; `insights.test` layer order | PASS |
| Numbers formatted correctly across report types | `formatBreakdownValue`/`humanizeBreakdownKey`; 9 assertions | PASS |
| Ask-GO posts owner Q, shows operator replies, no fake AI | `AskThread` (post + poll), honest microcopy | PASS |
| Nav gated on `insights:read` (staff/viewer included) | `roles.ts` NAV + `roles.test` visibleNav | PASS |
| No vertical nouns in the UI (rule zero) | `scripts/guards.py` industry-nouns → 0 | PASS |

**Commands:** oxlint clean (pre-existing warnings only) · `tsc -b --noEmit` OK · **`vitest` 47 pass**
· `npm run build` OK · **guards 0** · backend unchanged: `ruff` clean · `mypy core` **136** ·
**`pytest tests/unit tests/isolation` 384 pass** (no regression).

**Next recommended action:** founder review → merge to main + push + record hash + verify CI. Then the
**security-hardening ticket** (close audit gaps d/e) per the vision-intake sequence. **The Phase
3.5-eng analytics/intelligence engine (A1–A4.6) is complete.**

---

## 2026-08-08 — Security-hardening S1: secret scanning (audit #16a)

**Branch:** `feature/security-s1-secret-scan` (off main). **Merge:** `bc6e4a6`. First of three
security sub-tickets (S1 secret-scan → S2 error tracking → S3 backup/restore), per the founder-approved
sequence in DECISIONS 2026-08-08.

**Recon (read-only) verdict — audit #16a is CLEAN.** A full gitleaks history scan (134 commits,
3.4 MB) surfaced exactly **one** finding, confirmed a **false positive**: `core/packs/bundle.py:175`
`private_key: Ed25519PrivateKey` is a keyword-only *function parameter type annotation* (for passing a
signing-key object at runtime), not a credential. No real secret has ever been committed. `.env` and
`secrets/*.yaml` are gitignored; only SOPS `*.enc.yaml` + `*.example.yaml` are tracked (both safe).

**Made it permanent (so #16a stays proven, not assumed):**
- `.gitleaks.toml` — extends the default ruleset; a **tight** allowlist covering only (a) the confirmed
  `private_key: Ed25519PrivateKey` annotation (matched against the whole line, so a real key can't slip
  through) and (b) SOPS `*.enc.yaml` by path (encrypted-by-design, high-entropy ciphertext).
- CI `secret-scan` job — pinned **gitleaks 8.30.1** binary (no third-party action → supply-chain safe),
  `fetch-depth: 0` for **full-history** coverage, `--redact` so a finding never prints the secret into
  CI logs. Fails the build on any real leak.
- `make secret-scan` — the same scan locally.

**Verification:** history scan **clean** (exit 0) with the config; working-tree scan **clean**; a
planted fake GitHub PAT is **still caught** (leaks found: 1 → non-zero → CI fails), proving the
allowlist is narrow, not a blanket suppression; the annotation remains ignored. CI YAML validated
(6 jobs). No Python/web/test files touched → engine + suites unaffected.

**Security note:** no secret value was ever printed during this work — `--redact` on every scan; the
one finding was inspected by reading the source line, not the scanner's raw match.

**Next recommended action:** founder review → merge + push + record hash + verify CI. Then **S2 —
error/exception tracking**, decided as **self-hosted GlitchTip** (founder: "both best UX and tight" →
Sentry-grade dashboard with error data staying on our cloud).

---

## 2026-08-08 — Security-hardening S2: error tracking via self-hosted GlitchTip (audit #16d)

**Branch:** `feature/security-s2-error-tracking` (off main). **Merge:** `02038dd`. Second of the three
security sub-tickets. Founder-approved dependencies (CLAUDE.md §9): **`sentry-sdk`** (Python, MIT) +
**`@sentry/react`** (JS, MIT) — the standard clients for GlitchTip's Sentry-compatible ingest.

**Both halves: best UX + tight.** GlitchTip is **self-hosted** → the Sentry-grade dashboard, with
error data never leaving our cloud (no third-party SaaS). And it is **off by default + PII-scrubbing**:

- **Backend** — `core/common/error_tracking.py` (`setup_error_tracking(app)`, wired in `main.py` next
  to `setup_telemetry`). Inert unless `GROWTH_OPERATOR_ERROR_TRACKING_DSN` is set (with no DSN,
  `sentry_sdk` is never even imported). When set: `send_default_pii=False`,
  `max_request_body_size="never"`, `include_local_variables=False`, and a `before_send` that strips
  request bodies/cookies + redacts `Authorization` and scrubs phone/OTP (reusing `telemetry.scrub`) +
  email + bearer/JWT tokens across the whole event. Config field `error_tracking_dsn` (default None).
- **Frontend** — `web/src/lib/errorTracking.ts` (`initErrorTracking` gated on `VITE_ERROR_DSN`, same
  scrubbing in `beforeSend`/`beforeBreadcrumb`) + `components/ErrorBoundary.tsx` (calm recovery screen
  + `reportError`, a no-op when tracking is off) wrapping the app in `main.tsx`.
- **Infra/UX** — `infra/docker/docker-compose.glitchtip.yml` (standalone stack, own PG+Redis, port
  8888) + `infra/docker/GLITCHTIP.md` runbook + `make glitchtip` + `web/.env.example`.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Off by default (no DSN → nothing initialized, nothing sent) | `test_setup_is_inert_without_dsn`; frontend `initErrorTracking()===false` | PASS |
| When on, init is *tight* (no PII, no body/locals, before_send wired) | `test_setup_initializes_tightly_with_dsn` | PASS |
| Backend scrubber masks phone/OTP/email/tokens + drops sensitive keys | `test_scrub_text_*`, `test_scrub_obj_*` | PASS |
| Request body dropped + auth header redacted before send | `test_before_send_strips_request_body_*` | PASS |
| Frontend scrubber masks PII + drops sensitive keys | `errorTracking.test.ts` scrubText/scrubDeep | PASS |

**Commands:** backend — `ruff` clean · `mypy core` **137** · guards 0 · `uv sync --frozen` in sync ·
`pytest tests/unit tests/isolation` **389** (5 new). web — oxlint clean · `tsc` OK · `vitest` **50**
(3 new) · build OK. gitleaks: **no leaks** (new files don't trip the S1 scanner). GlitchTip compose
validates.

**Security note:** the whole point of S2 is that error reports carry NO customer PII or credentials —
proven by the scrubber tests. Off by default, so it introduces no external side-effect until a DSN is
deliberately set to point at our own GlitchTip.

**Next recommended action:** founder review → merge + push + record hash + verify CI. Then **S3 —
backup + tested restore runbook** (audit #16e), the last security sub-ticket.

---

## 2026-08-08 — Security-hardening S3: backup + tested restore (audit #16e) — security initiative complete

**Branch:** `feature/security-s3-backup-restore` (off main). **Merge:** `2b4182f`. Last of the three
security sub-tickets. **No app code / migration / dependency** — scripts + CI + docs only.

**#16e was "backups that have never been restored" — so the deliverable is the RESTORE DRILL, not just
a dump.** An untested backup is a false sense of safety.
- `scripts/db_restore_drill.sh` — dumps the live DB → restores into a throwaway scratch DB → verifies
  the restore matched on **table count + `alembic_version` + `organizations` row count** → drops the
  scratch DB → prints PASS/FAIL (non-zero on mismatch). Only ever touches its own scratch DB.
- **Runs in CI** (`migrate` job, right after `alembic upgrade head`) on every push (founder-approved) —
  the restore path is proven **continuously**, which is what actually closes #16e.
- Locally: `make backup-drill` pipes the drill into the dev Postgres container → works with just
  Docker, no host pg tools. **Verified against pg16: 71 tables, head `9f9334d2999a`, orgs
  round-tripped → PASS.**
- `scripts/db_backup.sh` (pg_dump custom format → gitignored `./backups`); `scripts/db_restore.sh`
  (drop+recreate a target, **guardrails**: refuses `*prod*`, refuses the primary DB without `--force`);
  `make backup` / `restore` / `backup-drill`; `infra/db/BACKUP_RESTORE.md` runbook; `/backups/` +
  `*.dump` gitignored (dumps hold real data).

**Requirement → evidence:**
| Criterion | Evidence | Result |
|---|---|---|
| A backup demonstrably restores (not assumed) | `db_restore_drill.sh` run vs pg16 → PASS (71 tbl / head / orgs) | PASS |
| Restore is proven continuously | CI `migrate` job runs the drill every push | PASS (verify on merge) |
| Restore can't clobber prod/primary by accident | `db_restore.sh` guardrails (`*prod*`, `--force`) | PASS |
| Dumps never committed | `.gitignore` `/backups/` + `*.dump`; gitleaks clean | PASS |

**Commands:** `bash -n` all three scripts OK · CI YAML valid (drill step in `migrate`) · **gitleaks
no-leaks** (dev connection strings don't trip) · guards 0 · **`make backup-drill` → PASS**. Production
automation (scheduled/off-site/encrypted/PITR/scheduled-drill) deferred to PRODUCTION_DEPTH_BACKLOG.

**Next recommended action:** founder review → merge + push + record hash + verify CI (the CI `migrate`
job will run the drill natively with host pg tools). **This completes the security-hardening
initiative (S1 secret scan + S2 error tracking + S3 backup/restore) — audit #16 a/d/e closed; b/c/g
were already strong; f (payments) N/A.**

---

## 2026-08-08 — Phase 4 P4.1: operator console foundation — cross-store roster

**Branch:** `feature/phase4-p41-tenant-roster` (off main). **Merge:** `98ecb05`. First Phase-4 ticket;
the backbone the GO business dashboards hang off. **Founder-approved sequence:** foundation (roster)
first, then the real-data dashboards P4.2–P4.5, then P4.6 financial/sales (needs a billing model).

**The security shape (this is the cross-tenant surface, handled deliberately):** a cross-store roster
must read RLS-protected per-store tables, but we refused to widen the `app.platform_admin` flag (that
would break the least-privilege lock). Instead — migration 029 adds **`platform_tenant_roster()`, a
`SECURITY DEFINER` function** (same controlled pattern as `resolve_report_org`/028) that returns ONLY
curated registry + count fields per store — `org_id, name, plan, status, created_at, paused,
open_tickets, member_count` — and **never customer PII** (no contacts, messages, revenue). It runs
with definer privilege to count `tenant_settings`/`support_tickets`/`user_orgs`, so the flag allowlist
is unchanged and `test_platform_admin_scope` stays green (verified). `GET /v1/admin/tenants`
(`core/tenancy/tenants_admin.py`) is gated on `platform.tenants:read` + the admin plane and **audits
every listing** to `platform_access_log` (cross-tenant reads are audited, not just writes). web-ops
`StoresSection` renders the roster table (name · active/paused · plan · members · open tickets),
replacing the placeholder; nav was already gated on `platform.tenants:read`.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Roster reflects real per-store state (paused + counts) | `test_roster_reflects_real_store_state` (paused=true, open=1, members=1) | PASS |
| Roster exposes ONLY curated fields — no customer PII | `test_roster_exposes_only_curated_fields_no_pii` (exact field set + forbidden-substring scan) | PASS |
| Non-operator can't read the roster | `test_roster_403_for_non_operator` | PASS |
| Unauthenticated → 401; plane off → 404 | `test_roster_401_without_token`, `test_roster_404_when_plane_disabled` | PASS |
| Cross-tenant flag NOT widened (lock intact) | `tests/isolation/test_platform_admin_scope` (4 pass) | PASS |

**Commands:** backend — `ruff` clean · `mypy core` **138** · guards 0 · **`pytest tests/unit
tests/isolation tests/integration/test_tenants_admin.py` 394** (5 new) · migration 029 up/down
round-trip · restore drill still PASS at head. web-ops — oxlint clean · `tsc` OK · `vitest` 6 · build
OK. gitleaks: no leaks. No customer-`web/` change.

**Next recommended action:** founder review → merge + push + record hash + verify CI. Then **P4.2 —
Operational dashboard** (what's breaking/delayed across the platform).

---

## 2026-08-08 — Phase 4 P4.2: operational dashboard ("what's breaking / delayed")

**Branch:** `feature/phase4-p42-operational` (off main). **Merge:** `de937de`. Second Phase-4 ticket;
reuses the curated-SECDEF cross-store pattern from P4.1 (DECISIONS 2026-08-08).

**Migration 030** `platform_operational_health()` **SECURITY DEFINER** function → a SINGLE row of
platform-wide COUNTS, never any store's rows or PII: `outbox_pending`, `outbox_stuck` (unpublished
> 5 min), `approvals_pending`, `approvals_overdue` (pending past expiry), `tickets_open`,
`tickets_urgent` (open + priority=urgent or severity=critical), `stores_paused`. It reads the
RLS-protected `approvals`/`support_tickets`/`tenant_settings` via definer privilege plus the RLS-free
`event_outbox`, so the `app.platform_admin` flag allowlist is **unchanged** (least-privilege lock
green). `GET /v1/admin/ops/health` (`core/tenancy/ops_admin.py`, `platform.tenants:read` + admin
plane, **audited**). web-ops `OperationalSection` at `/ops` — severity-colored health cards; error
DETAIL is explicitly deferred to the self-hosted GlitchTip (S2), so we don't fake an error count.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Health counts move by EXACTLY what we seed | `test_health_counts_move_by_exactly_what_we_seed` (before/after deltas) | PASS |
| Overdue ≠ pending (query discriminates) | same test: pending Δ=2, overdue Δ=1 | PASS |
| Gated: non-operator 403 / no-token 401 / plane-off 404 | `test_health_403…` / `_401…` / `_404…` | PASS |
| Flag allowlist NOT widened (lock intact) | `tests/isolation/test_platform_admin_scope` (4 pass) | PASS |

**Commands:** backend — `ruff` clean · `mypy core` **139** · guards 0 · **`pytest` 398** (4 new) ·
migration 030 up/down round-trip · restore drill still PASS at head. web-ops — oxlint clean · `tsc`
OK · `vitest` 6 (nav pins updated) · build OK. gitleaks: no leaks. No customer-`web/` change.

**Next recommended action:** founder review → merge + push + record hash + verify CI. Then **P4.3 —
Executive + Marketing** (aggregate the per-store analytics engine across all stores; needs a curated
cross-store analytics projection, same SECDEF discipline).

---

## 2026-08-08 — Phase 4 P4.3: executive + marketing (cross-store analytics rollup)

**Branch:** `feature/phase4-p43-analytics-rollup` (off main). **Merge:** `97663fc`. Third Phase-4
ticket; reuses the curated-SECDEF cross-store pattern (029/030).

**Migration 031** `platform_analytics_rollup(p_days)` **SECURITY DEFINER** function → a SINGLE row of
platform-wide SUMS/COUNTS over the last `p_days` **and the prior `p_days`** (for week-over-week) —
never any store's rows or PII. Executive: `revenue_minor`, `orders`, `leads`, `quotes` (each with a
`*_prev`), `active_stores`. Marketing: `campaigns_run`, `messages_sent`, `campaigns_analyzed`,
`attributed_revenue_minor` (summed from `agent_reports.full_breakdown`). Aggregates the RLS-protected
`business_metrics`/`campaigns`/`agent_reports` via definer privilege, so the `app.platform_admin` flag
allowlist is **unchanged** (least-privilege lock green). `GET /v1/admin/analytics/rollup?days=`
(`core/tenancy/analytics_admin.py`, `platform.tenants:read` + admin plane, **audited**). web-ops
`AnalyticsSection` at `/analytics` — Executive WoW cards + Marketing cards.

**Honest scope:** **CAC & churn** (Executive) and **impressions & CPL** (Marketing) need per-client
billing + ad-platform data we don't capture yet — shown as explicit "deferred" notes, **not faked**.
CAC/churn land with the billing model (P4.6).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Aggregates move by EXACTLY what we seed (current window) | `test_rollup_aggregates_move_by_exactly_what_we_seed` | PASS |
| Prior-window (WoW) is separate | same test: `revenue_minor_prev` Δ from a 10-day-ago row | PASS |
| Campaign + attributed-revenue aggregation | same test: campaigns_run/messages_sent/analyzed/attributed | PASS |
| Gated: non-operator 403 / no-token 401 / plane-off 404 | `test_rollup_403…` / `_401…` / `_404…` | PASS |
| Flag allowlist NOT widened (lock intact) | `tests/isolation/test_platform_admin_scope` (4 pass) | PASS |

**Commands:** backend — `ruff` clean · `mypy core` **140** · guards 0 · **`pytest` 402** (4 new) ·
migration 031 up/down round-trip · restore drill still PASS. web-ops — oxlint clean · `tsc` OK ·
`vitest` 6 (nav pins updated) · build OK. gitleaks: no leaks. No customer-`web/` change.

**Next recommended action:** founder review → merge + push + record hash + verify CI. Then **P4.4 —
Customer success** (ticket trends + at-risk stores) and **P4.5 — per-store drill-down**.

---

## 2026-08-08 — Phase 4 P4.4: customer success (store health + at-risk stores)

**Branch:** `feature/phase4-p44-customer-health` (off main). **Merge:** `c1e202d`. Fourth Phase-4
ticket; reuses the curated-SECDEF cross-store pattern.

**Migration 032** `platform_customer_health()` **SECURITY DEFINER** function → ONE row PER STORE of
aggregate health — never any store's customer rows or PII: `paused`, `open_tickets`, `urgent_tickets`,
`resolved_7d`, `days_since_activity` (NULL if never active), `revenue_7d`, `revenue_prev_7d`, and a
computed **`at_risk`** = paused OR urgent tickets OR no activity > 14 days OR revenue halved WoW.
Aggregates the RLS-protected `support_tickets`/`business_metrics`/`tenant_settings` via definer, so the
`app.platform_admin` flag allowlist is **unchanged** (least-privilege lock green). `GET
/v1/admin/customer-health` (`core/tenancy/customer_health_admin.py`, `platform.tenants:read` + admin
plane, **audited**), rows sorted at-risk first. web-ops `CustomerSuccessSection` at `/health` — an
at-risk-first table with per-store reason chips (paused / N urgent / inactive / revenue drop) + a
trend header (at-risk / open / resolved-7d).

**Honest scope:** **NPS** (needs a survey mechanism) and **upsell** (needs plan/billing data) are
shown as explicit "not yet" notes, **not faked**. Upsell lands with the billing model (P4.6).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| `at_risk` fires for paused / urgent / inactive (each cause) | `test_at_risk_fires_per_cause_and_healthy_is_clear` | PASS |
| Healthy store stays NOT at-risk (negative case) | same test (recent activity, no tickets, not paused) | PASS |
| Gated: non-operator 403 / no-token 401 / plane-off 404 | `test_health_403…` / `_401…` / `_404…` | PASS |
| Flag allowlist NOT widened (lock intact) | `tests/isolation/test_platform_admin_scope` (4 pass) | PASS |

**Commands:** backend — `ruff` clean · `mypy core` **141** · guards 0 · **`pytest` 406** (4 new) ·
migration 032 up/down round-trip · restore drill still PASS. web-ops — oxlint clean · `tsc` OK ·
`vitest` 6 (nav pins updated) · build OK. gitleaks: no leaks. No customer-`web/` change.

**Next recommended action:** founder review → merge + push + record hash + verify CI. Then **P4.5 —
per-store drill-down** (operator opens a specific store's agent reports/reasoning, audited) — the last
real-data dashboard before P4.6 (financial/sales, which waits on the billing model).

---

## 2026-08-08 — Phase 4 P4.5: per-store drill-down (operator reads a store's agent reports)

**Branch:** `feature/phase4-p45-store-drilldown` (off main). **Merge:** `cde833e`. Fifth Phase-4 ticket
— the **last real-data dashboard**. Reuses the curated-SECDEF pattern, but tighter: this exposes a
store's actual insight CONTENT, not aggregate counts.

**Migration 033** two **SECURITY DEFINER** functions over one store's RLS-protected `agent_reports`:
`platform_store_reports(p_org)` (summaries) and `platform_store_report(p_org, p_report)` (full report,
**scoped to `org_id = p_org`** so a report id from another store can never resolve under the wrong
org). Flag allowlist unchanged (lock green). Two endpoints on the tenants-admin router:
`GET /v1/admin/tenants/{org}/reports` (list) + `/{report_id}` (detail), gated on
**`platform.insights:read`** (the purpose-built permission) + admin plane, and **each read audited to
`platform_access_log` with `target_org_id`** — a permanent record of which operator opened which
store's insights. web-ops: roster store names now link → `/stores/$orgId` `StoreReportsSection` —
verdict headlines → expand into drivers + numbers, operator-side (dark theme).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Operator reads a store's report list (only that store's) | `test_operator_reads_a_stores_reports_list` | PASS |
| Operator reads full report detail (drivers + breakdown) | `test_operator_reads_a_stores_report_detail` | PASS |
| **A report id from another store 404s under wrong org** | `test_report_id_from_another_store_404s_under_wrong_org` | PASS |
| Gated: non-operator 403 / no-token 401 / plane-off 404 | `test_reports_403…` / `_401…` / `_404…` | PASS |
| Flag allowlist NOT widened (lock intact) | `tests/isolation/test_platform_admin_scope` (4 pass) | PASS |

**Commands:** backend — `ruff` clean · `mypy core` **141** · guards 0 · **`pytest` 412** (6 new) ·
migration 033 up/down round-trip · restore drill still PASS. web-ops — oxlint clean · `tsc` OK ·
`vitest` 6 · build OK. gitleaks: no leaks. No customer-`web/` change.

**Next recommended action:** founder review → merge + push + record hash + verify CI. **The real-data
Phase-4 dashboards (P4.1–P4.5) are complete.** P4.6 (Financial + Sales) is BLOCKED on the per-client
billing model (see VISION_INTAKE item 17 + go-revenue-model): the revenue-vs-pass-through-cost
question must be answered and a lightweight billing/revenue record built before those numbers are real.

---

## 2026-08-08 — Campaign SEND execute path C1 (MVP-075 / diagram C5) — the top MVP gap

**Branch:** `feature/campaign-send-c1` (off main). **Merge:** `291dd68`. Backend only (C2 = the `web/`
wizard, next). Built to the **authoritative spec** (C5, MVP-075/089/066) in full — founder: "full
faithful version, don't defer anything."

**Why:** the MVP audit found campaigns could be **created + analyzed but never SENT** (`campaign.*`
events were never emitted; no compose→approve→send). This closes that loop — the analytics engine +
the P4.3 marketing dashboard now measure real broadcasts.

**Migration 034:** `campaign_sends` (+RLS) per-recipient ledger (contact, conversation, status
queued/sent/failed/skipped, reason, message_id); `campaigns` gains `template_key`/`template_lang`/
`halt_reason` and a widened status CHECK (`pending_approval`/`executing`/`halted`/`rejected`).

**The flow (all through the same gated `send()` — no bypass, gated-simulated):**
- `POST /v1/campaigns/{id}/send` with a **typed recipient count** → resolve audience (consented +
  un-suppressed, mirroring `send()`'s `_POSITIVE_CONSENT`) → **typed ≠ actual → 409** (real number
  shown, no silent fix) → **tier-3 approval**, campaign → `pending_approval`.
- On `approval.resolved` (new consumer group `campaign-send-exec`): approve → queue one `campaign_sends`
  row per contact + `executing` → **staggered fan-out ≤ `HOURLY_RATE`/hour**; per recipient
  get-or-create conversation + mint audit-cap + single-use execution token + gated `send()` template
  (`message_class='marketing'`) — **its consent/suppression gates are the per-send re-check**; a
  refusal → `skipped` (consent/suppression) or `failed`. When nothing queued → `record_execution` +
  emit `campaign.executed.v1` → `executed`. Reject → `rejected`, nothing sent.
- **Quality-halt:** an opt-out spike (> `OPTOUT_HALT_RATIO` of already-sent contacts now suppressed) or
  a red Meta `channels.quality_rating` → `halted` + `halt_reason` + a warning (S2 telemetry). The Meta
  rating is the one simulated input (null until real Meta), consistent with our other gated adapters.
- **`campaign_fanout`** hourly scheduler resumes the stagger until nothing is queued.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Full fan-out sends to consented/un-suppressed, marks executed | `test_approve_fans_out_and_marks_executed` (2/2 sent, executed, excluded get no row) | PASS |
| Typed-count mismatch is blocked (no silent fix) | `test_typed_count_mismatch_blocks` (actual=2) | PASS |
| Broadcast is tier-3 approval-gated | `test_send_creates_tier3_approval` | PASS |
| Reject sends nothing | `test_reject_sends_nothing` | PASS |
| Quality-halt on opt-out spike | `test_quality_halt_on_optout_spike` | PASS |

**Commands:** `ruff` clean · `mypy core` **143** · guards 0 · **`pytest` 407** (unit+isolation+all
campaign tests; 5 new) · migration 034 up/down round-trip · restore drill PASS · gitleaks no-leaks.

**Next recommended action:** founder review → merge + push + record hash + verify CI. Then **C2** — the
`web/` Campaigns wizard (audience preview → template → review with typed count → the parked approval
appears in the existing Approvals queue). Segment-targeting + real Meta remain follow-ups.

---

## 2026-08-08 — Campaign compose/send UI C2 (MVP-089) — completes the owner-facing broadcast loop

**Branch:** `feature/campaign-send-c2-web` (off main). **Merge:** `1356b53`. Frontend (`web/`) + one
tiny backend endpoint (the audience preview the typed-count gate needs a number for).

**Backend:** `GET /v1/campaigns/audience-preview` → `{audience_size}` (`campaigns:send`), declared
before `/{campaign_id}` so the literal path isn't captured as a campaign id. Uses
`audience.audience_count`.

**Frontend:** `CampaignsSection` at `/campaigns` — list (status badge + sent/failed + halt reason +
"awaiting approval" hint), a create form (name + a picker limited to **approved** WhatsApp templates →
`draft`), and the **send wizard**: it fetches the audience preview ("this will message N contacts"),
the owner **types N to confirm** (the C5 typed-count gate — a wrong number surfaces the 409's real
count), and Send parks the tier-3 approval, which then shows up in the existing **Approvals** queue for
approve/reject. Nav gated on `campaigns:read` — owner/manager/viewer see it, **staff does not** (RBAC);
the pinned `visibleNav` test was restructured to reflect that staff/viewer divergence (not weakened).

**Commands:** web — oxlint clean (pre-existing warnings only) · `tsc` OK · **`vitest` 50** · build OK.
backend — `ruff` clean · `mypy core` **143** · guards 0 · campaign api/send tests **12** · app imports
clean (scaffold). gitleaks: no leaks.

**Next recommended action:** founder review → merge + push + record hash + verify CI. **This completes
the campaign-send feature (C1 backend + C2 UI) — the top original-MVP gap is closed.** Then the
per-client billing model (unblocks P4.6), then bulk import / workflows / the marketing-agent layer.

---

## 2026-08-08 — Per-client billing model B1 (unblocks P4.6 Financial)

**Branch:** `feature/billing-b1` (off main). **Merge:** `4e06f82`. Backend only (B2 = the web-ops
Financial dashboard). Founder-approved model (DECISIONS 2026-08-08): service charges = **managed budget
+ margin**; subscription = **named tiers/plans**.

**Migration 035:** `billing_plans` (GO tier catalog — global, no org_id, admin-plane only);
`billing_subscriptions` (org-scoped RLS; one active plan/client via a partial unique index);
`billing_charges` (org-scoped RLS; **amount_minor** client pays + **cost_minor** we pay → margin =
amount − cost); `platform_billing_rollup()` **SECURITY DEFINER** aggregate → MRR (Σ active plan
prices) + this-month charge revenue/cost/**margin** + active-client count (sums only, never a client's
rows).

**Operator API `/v1/admin/billing/*`** (`core/billing/`) — admin-plane gated, `platform.tenants:manage`
(writes) / `:read` (reads): plans (create/list), per-client subscription (assign/cancel/get), per-client
charges (record/list), and the rollup. **Billing is operator-owned — there is NO tenant endpoint.** A
subscription/charge write is a **scoped** write (session set to the target org — no `app.platform_admin`
flag) and is **audited with `target_org_id`**; the cross-client rollup is the curated SECDEF (so the
flag allowlist stays `{support_tickets, insight_messages}` and the least-privilege lock stays green).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Margin = amount − cost; MRR from active plan; rollup adds up | `test_plan_subscription_charge_and_rollup_margin` (deltas) | PASS |
| A client's charges are org-isolated (RLS) | `test_a_clients_charges_are_org_isolated` | PASS |
| Operator-only: non-operator 403 / no-token 401 / plane-off 404 | `test_billing_403/401/404` | PASS |
| Flag allowlist NOT widened (lock intact) | `tests/isolation/test_platform_admin_scope` (4 pass) | PASS |

**Commands:** `ruff` clean · `mypy core` **146** · guards 0 · **`pytest` 394** (5 new) · migration 035
up/down round-trip · restore drill PASS · gitleaks no-leaks.

**Next recommended action:** founder review → merge + push + record hash + verify CI. Then **B2** — the
**P4.6 Financial** dashboard in `web-ops` (MRR / revenue / margin / active clients from the rollup).
The **Sales** dashboard remains a separate later ticket (needs its own GO-sales-pipeline model).

---

## 2026-08-08 — P4.6 Financial dashboard B2 (web-ops) — makes billing visible

**Branch:** `feature/billing-b2-financial` (off main). **Merge:** `01e2646`. Frontend-only (`web-ops`)
— consumes the B1 billing endpoints; no backend change.

`FinancialSection` at `/financial` (nav gated `platform.tenants:read`): **rollup cards** — MRR,
this-month service revenue, cost, **margin**, active clients (from `platform_billing_rollup`); a
**plans** manager (list + create); and **per-client billing** — a store picker → its active plan +
assign a plan + record a charge (₹ client pays + ₹ our cost) + list its charges. Write actions
(create plan / assign / record charge) are gated on `platform.tenants:manage` in the UI (dev/admin
operators) and enforced server-side (B1). web-ops nav pins updated (Financial visible to all operator
roles that hold `tenants:read`). Honest note in-UI: cashflow / burn / runway need expense + cash
inputs we don't capture yet — deferred; this shows revenue (MRR + service margin).

**Commands:** web-ops — oxlint clean · `tsc` OK · **`vitest` 6** (nav pins) · build OK. guards 0.
gitleaks: no leaks. Backend unchanged.

**Next recommended action:** founder review → merge + push + record hash + verify CI. **This completes
the P4.6 Financial dashboard (billing B1 + B2).** The **Sales** dashboard remains a separate later
ticket (a GO-sales-pipeline model — prospect→onboarded — distinct from billing). Then, per the founder
sequence: bulk catalog import (MVP-077–080) / workflow engine (MVP-071–73) / marketing-agent layer.

---

## 2026-08-08 — Bulk import I1 / MVP-078: CSV/XLSX extraction + column mapping

**Branch:** `feature/ingest-i1-extract-csv` (off main). **Merge:** `ca763a0`. First sub-ticket of the
bulk-import track (MVP audit gap "catalog is imported"). The batch upload + state machine (MVP-076)
existed; this fills the **extract** stage for structured sheets. Photo/vision extraction (MVP-077)
stays gated-simulated (needs a real vision LLM). No migration (uses `import_rows`/`import_batches`).

**`core/ingestion/extract_csv.py`:** loads the batch's uploaded bytes → parses **CSV** (stdlib) or
**XLSX** (openpyxl) → writes one `import_rows` per data row: `raw` (original) + `normalized` (mapped
to catalog fields — name/sku/price/desc → title/sku/base_price_minor/description, price ₹→minor,
unmapped columns → `attributes`) + `flags` (`missing_title`, `unparsed_price:*`) + a coarse
`confidence`. **Header→field map is remembered per source signature** (header-tuple hash) directly in
`tenant_settings` (the settings service only knows registered keys, so mapping is stored/loaded via
small helpers) — the same sheet maps automatically next time. State advances `extracting→extracted`;
any failure → `failed` (resumable) with the reason, surfaced as `ExtractionFailed`.
`POST /v1/imports/{id}/extract` (`catalog:write`) triggers it (409 if not extractable, 404 unknown).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| CSV → mapped rows; price→minor; unmapped→attr; title-less flagged | `test_csv_extraction_maps_fields_and_flags_missing_title` | PASS |
| XLSX parses via openpyxl | `test_xlsx_extraction_via_openpyxl` | PASS |
| Saved per-signature mapping overrides the auto-map | `test_saved_mapping_overrides_the_auto_map` | PASS |
| Extraction failure → batch `failed` (resumable) | `test_extraction_failure_marks_batch_failed` | PASS |

**Dependency (founder-approved, §9):** `openpyxl` (MIT) for `.xlsx`; CSV uses the stdlib. mypy override
added (no stubs). **Commands:** `ruff` clean · `mypy core` **147** · guards 0 · `uv sync --frozen` in
sync · **`pytest` 393** (4 new) · gitleaks no-leaks. No migration.

**Next recommended action:** founder review → merge + push + record hash + verify CI. Then **I2 /
MVP-079** — the review queue (confirm/edit/reject rows) — then **I3 / MVP-080** (load + 30-day revert).

---

## 2026-08-09 — Bulk import I2 / MVP-079: review queue

**Branch:** `feature/ingest-i2-review` (off main). **Merge:** `b4bc5c2`. Second bulk-import sub-ticket.
No migration, no dependency (uses `import_rows.state`/`flags`).

**`core/ingestion/review.py`:** `validate_batch` advances `extracted → validating → review`, flagging
each row — `missing_title` (blocking: can't confirm/load until edited) and `duplicate_sku` (a sku that
already exists in the active catalog OR repeats within the batch; non-blocking — the owner decides).
Then per-row `confirm_row` (refuses a blocking row), `edit_row` (correct fields → re-flag → confirm if
clean), `reject_row`, and `confirm_all` (bulk — skips rejected + blocking). An **auto-approve** gate
confirms the whole batch only when every row is ≥0.95 confidence with no flags AND a ≥5% sample was
human-confirmed first. Endpoints on `/v1/imports/{id}` (`catalog:write`): `POST /validate`,
`POST /rows/{seq}/confirm`, `POST /rows/{seq}/reject`, `PATCH /rows/{seq}`, `POST /rows/confirm-all?auto=`.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| validate flags missing-title + duplicate-sku, moves to review | `test_validate_flags_and_review_lifecycle` | PASS |
| blocking row can't confirm until edited; reject works | same test (confirm raises, edit→confirmed, reject) | PASS |
| bulk confirm-all skips rejected + blocking | `test_bulk_confirm_skips_rejected_and_blocking` | PASS |
| auto-approve needs high confidence + a confirmed sample | `test_auto_approve_needs_high_confidence_and_a_sample` | PASS |

**Commands:** `ruff` clean · `mypy core` **148** · guards 0 · **`pytest` 396** (3 new) · gitleaks
no-leaks. No migration.

**Next recommended action:** founder review → merge + push + record hash + verify CI. Then **I3 /
MVP-080** — load (confirmed rows → catalog items, identity dedupe, import_batch_id stamp) + 30-day
revert — the stage that actually creates the catalog items. Then I4 (MVP-077 photo, gated).

---

## 2026-08-09 — Bulk import I3 / MVP-080: load + 30-day revert

**Branch:** `feature/ingest-i3-load` (off main). **Merge:** `79adc2f`. The payoff stage — confirmed
rows become catalog items, reversibly. **No migration** (`catalog_items.import_batch_id` already
existed from mig 012; `crud.create_item` already accepts `import_batch_id`).

**`core/ingestion/load.py`:**
- `load_batch` — advance `review → loading`; for each **confirmed** row build an `ItemInput`
  (title/sku/base_price_minor/description/attributes, `price_mode='static'`) and call
  `crud.create_item` (which validates attributes + enforces the pack's identity keys), stamped with
  `import_batch_id`. `DuplicateIdentity` → row `skipped_duplicate`; `ValidationProblems` → row
  `load_failed` (both per-row, batch continues). Finish `loading → loaded`. Returns loaded/skipped/
  failed counts.
- `revert_batch` — within the **30-day** window (from the batch's last state-change), archive
  (`status='archived'`) this batch's items that are **unmutated** (`updated_at = created_at`); an
  edited-since item is left alone and listed in `mutated_skipped`. Finish `loaded → reverted`.
- `reap_old_batches` — daily `import_batch_reaper` job frees staging data (rows + blob ref) for
  terminal batches (loaded/reverted/cancelled/failed) older than the window; the loaded items stay.
- Endpoints `POST /v1/imports/{id}/load` + `/revert` (`catalog:write`).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Confirmed rows → catalog items stamped import_batch_id; revert archives | `test_load_creates_stamped_items_then_revert_archives` (2 loaded, 2 archived) | PASS |
| A row whose sku already exists is skipped (identity dedupe) | `test_load_skips_a_duplicate_sku` (1 loaded, 1 skipped) | PASS |
| Revert leaves an edited-since item alone (mutated_skipped) | `test_revert_leaves_a_mutated_item_alone` | PASS |

**Commands:** `ruff` clean · `mypy core` **149** · guards 0 · **`pytest` 399** (3 new; scheduler pin
updated for `import_batch_reaper`) · gitleaks no-leaks. No migration.

**Next recommended action:** founder review → merge + push + record hash + verify CI. **This completes
the CSV/XLSX bulk-import path (I1 extract → I2 review → I3 load/revert) — a jeweler can upload a
spreadsheet and it becomes reviewable, loadable, revertable catalog items.** Then **I4 / MVP-077**
(photo/vision extraction, gated-simulated), then the workflow engine.

---

## 2026-08-09 — Bulk import I4 / MVP-077: photo extraction (gated-simulated)

**Branch:** `feature/ingest-i4-photo` (off main). **Merge:** `743b786`. Completes the bulk-import track.
No migration, no dependency.

**`core/ingestion/extract_photo.py`** — gated-simulated vision extraction (same `_gate()` posture as
the intelligence agents / embeddings / rates): provider **disabled** (default) → a deterministic
**simulated** row per image (`Photo item N`, confidence 0.5, `simulated_vision` flag) so a photo batch
still flows through review → load in dev/pilot-simulation; provider **enabled** but the vision worker
unwired → fail-closed `provider_unavailable`. The real sandboxed vision worker + the pack's hint set +
post-processing (logprob confidence, weight rules) land when a provider is wired. The
`POST /v1/imports/{id}/extract` endpoint now **dispatches by `source_kind`** (`photo` → vision, else
CSV/XLSX). Rule Zero honoured (no vertical nouns in `core/` — the guard caught + I fixed a "jewelry"
reference in the docstring).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Simulated extraction → placeholder rows (title/conf/flag) | `test_simulated_photo_extraction_produces_placeholder_rows` | PASS |
| Enabling the provider (vision unwired) fails closed | `test_photo_gate_fails_closed_when_provider_enabled` (`provider_unavailable`) | PASS |

**Commands:** `ruff` clean · `mypy core` **150** · **guards 0** (Rule Zero) · **`pytest` 391** (2 new) ·
gitleaks no-leaks. No migration.

**Next recommended action:** founder review → merge + push + record hash + verify CI. **This completes
the bulk-import track (I1 CSV/XLSX extract → I2 review → I3 load/revert → I4 photo, gated).** Then the
**workflow engine (MVP-071–73)** — the last original-MVP gap — then Sales dashboard + marketing-agent
layer.

---

## 2026-08-09 — MVP-072: Workflow DSL parser + guard library (engine foundation)

**Branch:** `feature/mvp-072-workflow-dsl` (off main). **Merge:** `78aa0ae`. **Option A approved**
(readable sugar; grammar frozen). The last empty original-MVP module (`core/workflows`) now has its
foundation; the executor is MVP-073.

**Migration 036** (`5992a9cbb631`, revises `2d4b307a495a`): four org-scoped tables — `workflow_defini
tions`, `workflow_runs`, `workflow_run_events`, `wait_subscriptions` — each `apply_rls()`. MVP-072
writes only `workflow_definitions`; the run tables land now so MVP-073's executor has schema. Up →
down (all 4 dropped) → up all verified against the live DB.

**`core/workflows/`:**
- **schema.py** — frozen DSL v1 jsonschema (7 generic step verbs; `additionalProperties:false` +
  single-key step object rejects any non-grammar verb). `parse_duration_s`.
- **parser.py** — `validate_dsl` → guard parse + mandated injection → **CEL trigger compile** (celpy,
  same compile-cache posture as the approvals engine) incl. **`… FOR '72h'` duration predicate split
  into a scheduler check spec**; branch `when` + concurrency `key` CEL syntax-checked at parse.
- **guards.py** — the **7 core guards** (`consent_valid` · `not_suppressed` · `within_send_window` ·
  `touch_cap` · `budget_ok` · `flag_on` · `tier_max`) as async predicates over real L2/L3 rows
  (suppressions, contacts.consent_status, messages/conversations, feature_flags, billing_charges,
  quiet_hours). **Fail-closed** without a subject. `inject_mandated_guards` (name-keyed, idempotent).
- **store.py** — `seed_definition` (upsert), internal `activate`/`deactivate`, `active_definitions_
  for_event` routing.

**Installer:** `_seed_workflows` (was a deferred no-op) now parses + seeds each pack workflow active;
a non-grammar file is logged + **skipped**, never fatal. `DEFERRED_STEPS = ()`. Closes the
workflows-half of BLOCKERS #14. Reference-pack fixups: `verticals/{jewelry,kirana}/install.yaml`
`deferred_steps → []`; `kirana/reorder_nudge.yaml` guards → block-style (YAML was splitting
`touch_cap(1, 7d)` on the inner comma — zero-semantic formatting fix).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| All grammar-conformant jewelry files parse | `test_conformant_jewelry_workflows_parse[visit/rate/festival]` | PASS |
| Kirana (modularity proof) parses | `test_kirana_workflows_parse` | PASS |
| Non-grammar verbs rejected (grammar freeze) | `test_proposed_workflow_with_ungrammar_verbs_is_rejected`, `test_unknown_step_type_rejected` | PASS |
| FOR-duration → check spec | `test_for_duration_trigger_compiles_to_check_spec` | PASS |
| Crafted def gets mandated guard injected | `test_mandated_guard_injected_when_omitted` | PASS |
| 7 guards over real state; fail-closed | `tests/integration/test_workflow_guards_db.py` (8 tests) | PASS |
| Seed + activate + route + upsert | `tests/integration/test_workflow_definitions.py` | PASS |
| Cross-tenant RLS + fail-closed | `tests/isolation/test_workflow_rls.py` | PASS |
| Migration up/down | `alembic upgrade head` / `downgrade -1` / `upgrade head` | PASS |

**Commands:** ruff clean · `mypy core` **154** · **guards 0** (Rule Zero) · **415** unit+isolation ·
**432** integration+e2e+contract · migration up/down/up verified. **No new dependency.**

**Next recommended action:** founder review → merge + push + record hash + verify CI. Then **MVP-073
executor + waits** (event-sourced runs, step idempotency, reply/duration waits, saga compensation,
concurrency policies), then the **Option-A diagnosis extension + jewelry ghost-recovery pack + eval
harness** (offline/synthetic, real-ready via the gate), then the **CAPTURE-GAP migrations** for live.
See DECISIONS 2026-08-09 (grammar freeze + Option A + ghost-recovery as MVP thesis).

---

## 2026-08-09 — MVP-073a: Workflow executor spine (stage 1 of the workflow-executor initiative)

**Branch:** `feature/mvp-073a-executor-spine` (off main). **Merge:** `8f393a7`. First rigorously-tested
stage of MVP-073, which the founder split into staged deliveries (DECISIONS 2026-08-09; the previously
fenced simulation / builder / owner-built parts are now in scope as later stages). No migration, no dep.

**`core/workflows/program.py`** — compiles a validated DSL into a **flat instruction list with jump
semantics** (SET/EMIT/AGENT/WAIT/HUMAN/BRANCH/JUMP/NOOP/END). `branch` → conditional jump, so a nested
`agent_task`/`wait` inside a branch is just another instruction at its own `pc`; the run cursor is a
single integer → crash-resume = "reload `pc`, continue". Each instruction carries a stable `sid`
(idempotency key). Verified against all three conformant jewelry workflows.

**`core/workflows/executor.py`** — runs the program as a program counter over the event-sourced tables
(`workflow_run_events` append + `cursor` advance in one tx). Two replay-safety invariants:
`agent_task` **releases the org session** before calling `runtime.executor.start_run` (which opens its
own per-org advisory-locked session — nesting would deadlock), then reopens to record the result;
**idempotency by `sid`** (a `step_completed` step is skipped on replay). Concurrency `drop` (block +
`workflow.skipped`) and `replace` (supersede live runs) implemented; `queue` deferred to stage 2.
`wait`/`human_task` **park** the run (`waiting`) — the wake wiring is stage 2/3. `agent_runner` is
injectable so tests stay hermetic (no runtime).

**`core/workflows/triggers.py`** — `match_and_start(org, event_type, payload)`: active defs for the
event → trigger-condition CEL → guards → start per policy. A guard block is a logged `workflow.skipped`
(never a silent lead-drop). Session released before `start_run` (deadlock-safe).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Compiler flattens branches, correct jump targets | `test_workflow_program.py` (4) | PASS |
| Synchronous workflow runs to completion (+ emit to outbox) | `test_synchronous_workflow_runs_to_completion` | PASS |
| agent_task runs via injected runner | `test_agent_task_runs_via_injected_runner` | PASS |
| **Crash resumes at cursor; completed step skipped (idempotent)** | `test_crash_resume_reruns_incomplete_and_skips_completed` | PASS |
| **Concurrency replace supersedes live run** | `test_concurrency_replace_supersedes_live_run` | PASS |
| Concurrency drop blocks new run | `test_concurrency_drop_blocks_new_run` | PASS |
| Trigger guard-block / false-condition → skip, no run | `test_workflow_triggers.py` (3) | PASS |

**Commands:** ruff clean · `mypy core` **157** · **guards 0** (Rule Zero) · **419** unit+isolation ·
**440** integration+e2e+contract. **+16 workflow tests.** No migration, no dependency.

**Next recommended action:** per founder ("rigorous testing + push each stage"), merge + push + record
hash + verify CI, then **stage 2 (073b): waits** (reply/duration/event + scheduler duration sweep +
match consumers + `queue` policy). Acceptance target there: reply at 95h matches, 97h times out.

---

## 2026-08-09 — MVP-073b: Workflow waits + queue policy (stage 2 of the workflow-executor initiative)

**Branch:** `feature/mvp-073b-waits` (off main). **Merge:** `626b822`. Long-running journeys survive
restarts and wait for replies / durations / events. Migration 037, no dependency.

**Migration 037** (`96b3c722a891`): adds `queued` to the `workflow_runs` status CHECK (additive) for the
queue concurrency policy. Up/down verified.

**`core/workflows/waits.py`** — on a WAIT park the executor now **registers a `wait_subscription`**:
reply → correlate on the subject's `conversation_id`; event → correlate on the wait's `event:` type;
duration → carry `fire_at`. `match_reply(org, conversation_id)` (inbound message wakes reply-waits) and
`match_event(org, event_type)` **atomically claim** the subscription (`UPDATE … WHERE status='pending'
RETURNING`) so a run wakes exactly once. **`sweep_waits`** (scheduler job `workflow_wait_sweep`, every
minute, cross-org via the `organizations` registry) **fires due duration waits** and **times out**
reply/event waits past `timeout_at`, waking the run with `wait.result='timeout'`.

**`core/workflows/executor.py`** — `wake_run(org, run, result)` resumes a parked run: set
`wait.result`, advance the cursor past the WAIT, drive. `queue` concurrency implemented (park behind a
live run as `queued`; `_promote_next` promotes the oldest queued run when the live one completes). The
CEL activation now promotes vars to top level so both `wait.result` and `vars.refresh_ok` DSL styles
resolve. **`core/workflows/consumer.py`** — a `msg.received` consumer group wakes reply-waits.
`wait` schema gains an optional `event:` field (event-waits can name their type).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| **Reply within window matches → reply path** | `test_reply_within_timeout_matches_and_takes_reply_path` | PASS |
| **Reply after timeout → timeout path (95h/97h boundary)** | `test_reply_after_timeout_takes_timeout_path` | PASS |
| Duration wait fires on the sweep | `test_duration_wait_fires_on_sweep` | PASS |
| Queue policy promotes on completion | `test_queue_policy_promotes_on_completion` | PASS |
| Duplicate wake is a no-op (wake-once) | `test_duplicate_wake_is_noop` | PASS |
| Sweep job registered | `test_scheduler_entrypoint` (`workflow_wait_sweep`) | PASS |

**Commands:** ruff clean · `mypy core` **159** · **guards 0** · **419** unit+isolation · **445**
integ+e2e+contract (+5) · migration 037 up/down. No dependency.

**Next recommended action:** merge + push + record hash + verify CI, then **stage 3 (073c): saga
compensation + human_task (approval) + ops run-timeline view**. Event-wait live fan-in (a generic event
consumer, shared with `triggers.match_and_start`) is a small follow-on when a workflow needs a live
event trigger — the matching logic is built + tested; only the Redis-stream wiring is deferred.

---

## 2026-08-09 — MVP-073c: Workflow saga + human_task + ops timeline (stage 3 of the executor initiative)

**Branch:** `feature/mvp-073c-saga-human` (off main). **Merge:** `b05ffd0`. Failure handling, HITL, and
the run-timeline read — `festival_campaign` (human approval + compensation block) is now fully runnable.
No migration, no dependency.

**Saga compensation** (`executor._compensate` / `_run_compensation`): an `agent_task` that **returns** a
failed status is a *business failure* → run `compensation.on_failure` (author-ordered, the reverse of
the effects to undo) as a mini-program (SET/EMIT/AGENT; WAIT/HUMAN skipped — a compensator must not
block), emit the `alert` (`alert.ops.v1`), mark the run `compensated` (or `compensated_partial` if a
compensator itself fails); no compensation block → `failed`. A **raised exception** stays a crash
(propagates, resumable) — the two are deliberately distinct.

**`human_task`** — the HUMAN step parks the run and raises an **approval** via
`approvals.service.create_approval` (action `workflow.human_task`, tier 2; the workflow run is linked in
the approval **payload** because `approvals.run_id` FKs `agent_runs`). A new `approval.resolved.v1`
consumer group (`workflow-human-task`) calls `executor.resume_human`: **approve** advances past the step
to the gated action; **reject** routes to compensation and never runs the gated action.

**Ops timeline** — `core/workflows/timeline.py` (`get_run_timeline` = run state + ordered event log;
`list_runs`) + `core/workflows/api.py` tenant router `GET /v1/workflows/runs` and `/runs/{id}`
(`insights:read`), registered in the app.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| **Compensators run in reverse (authored) order + alert** | `test_agent_failure_runs_compensators_in_authored_order` | PASS |
| No compensation block → failed | `test_failure_without_compensation_marks_failed` | PASS |
| human_task parks + raises a linked approval | `test_human_task_parks_and_raises_linked_approval` | PASS |
| Approve advances to the gated step | `test_approve_advances_to_gated_step` | PASS |
| Reject never runs the gated step | `test_reject_never_runs_gated_step` | PASS |
| Run timeline reads state + events | `test_run_timeline_reads_state_and_events` | PASS |

**Incidental fix (unrelated to workflows):** `tests/integration/test_business_metrics.py` used
`date.today()` (local) against `datetime.now(UTC)` seeding — a UTC/local off-by-one that the mid-session
date rollover exposed (it passed during stages 1–2). Aligned the test to the UTC basis. Confirmed
pre-existing: it fails on main with the stage-3 work stashed.

**Commands:** ruff clean · `mypy core` **161** · **guards 0** · **419** unit+isolation · **451**
integ+e2e+contract (+6) · no migration/dep.

**Next recommended action:** merge + push + record hash + verify CI, then **stage 4: simulation mode**
(`POST /v1/workflows/{id}/simulate` — replay historical events in a dry-run shadow, report would-have-
fired / guard-block / sample messages / cost) — the stage that directly serves the ghost-recovery proof.
The engine is now fully runnable (trigger → steps → agents → waits → approvals → compensation).

---

## 2026-08-09 — MVP-073d: Workflow simulation mode (stage 4 of the executor initiative)

**Branch:** `feature/mvp-073d-simulation` (off main). **Merge:** `ac17f0d`. The pre-activation dry-run —
"prove it works before going live" — which directly serves the ghost-recovery thesis. No migration/dep.

**`core/workflows/simulate.py`** — `simulate(session, org, definition_id, window_days=30)` replays the
org's historical `event_outbox` rows of the definition's trigger type over the window against the
definition, entirely **read-only** (no run, no send, no event — the shadow/dry-run the spec requires).
Report: `candidates` → `condition_filtered`/`condition_passed` (trigger CEL) → `would_have_fired`
(condition AND guards) → `guard_blocks` (`{guard: count}`) → `estimated_cost_minor` (`would_have_fired
× agent_steps_per_fire × cost_per_message`, an upper bound; agents are gated-simulated) → synthetic
`sample_messages`. Guards read current L2/L3 state (a point-in-time projection, noted in the report).
`POST /v1/workflows/{id}/simulate {window_days}` (`insights:read`), 404 on unknown definition.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Report accuracy (fired / condition-filtered / guard-block / cost) | `test_simulate_reports_fired_blocked_and_cost` (5 candidates → 1 filtered → 3 fired, `{not_suppressed:1}`, cost 300) | PASS |
| **Zero side effects** (no run, no event emitted) | `test_simulate_has_no_side_effects` | PASS |

**Commands:** ruff clean · `mypy core` **162** · **guards 0** · **419** unit+isolation · **453**
integ+e2e+contract (+2) · no migration/dep.

**Next recommended action:** merge + push + record hash + verify CI, then **stage 5: builder UI (H2)** —
the React flow-graph editor emitting DSL (server-side validation = the parser we already ship), then
**stage 6: owner-built / trust-ledger path**. After the engine stages, the **Option-A ghost-recovery
diagnosis extension** (readable sugar verbs → generic steps) + the jewelry pack (taxonomy, prompt,
templates) + eval harness (offline/synthetic) + CAPTURE-GAP migrations for live.

---

## 2026-08-09 — MVP-073e: Owner-built authoring backend (stage 5a — builder's server truth)

**Branch:** `feature/mvp-073e-authoring` (off main). **Merge:** `772fed1`. The backend half of the
builder (stage 5): validate + save owner-built definitions. The React editor is stage 5b (web/, the
store-owner console). No migration, no dependency.

**`core/workflows/authoring.py`** — `validate_owner_dsl` parses the DSL as an owner-built definition:
platform **mandated guards injected** (`OWNER_MANDATED_GUARDS = [not_suppressed]`), and any **`emit`
step refused** (owners cannot forge platform events — checked recursively incl. branch/compensation).
`create_owner_definition` saves it `origin='owner_built'`, `status='draft'` (never active on creation)
under a **complexity budget** (≤ `MAX_OWNER_DEFINITIONS = 10` per tenant). `update_owner_definition`
re-validates + replaces the draft's DSL in place; `list_owner_definitions` for the builder list view.
First activation stays gated on simulation + tier-2 approval + the trust ledger (stage 6).

**API** (`core/workflows/api.py`, `catalog:write` — the "configure my store" write perm owner+manager
hold, staff don't): `POST /v1/workflows/definitions/validate` (server-truth validation for the builder,
returns `{valid, error}`), `POST /definitions` (201, draft), `PUT /definitions/{id}`, `GET /definitions`
(422 on a guardrail violation).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Validation injects the mandated guard | `test_validate_injects_mandated_guard` | PASS |
| Rejects a non-grammar step | `test_validate_rejects_non_grammar_step` | PASS |
| **Owners cannot emit platform events** (even nested) | `test_validate_rejects_owner_emit` | PASS |
| Create → owner_built draft + guard | `test_create_saves_owner_built_draft_with_guard` | PASS |
| **Complexity budget caps at 10** | `test_complexity_budget_caps_creation` | PASS |
| Update replaces the DSL in place (stays draft) | `test_update_replaces_dsl` | PASS |

**Commands:** ruff clean · `mypy core` **163** · **guards 0** · **419** unit+isolation · **459**
integ+e2e+contract (+6) · no migration/dep.

**Next recommended action:** merge + push + record hash + verify CI, then **stage 5b: the builder UI**
(React, in `web/`) — a structured editor that composes the DSL and round-trips through the validate/
save endpoints (server truth), with the web gate (oxlint + tsc + build). Then stage 6 (owner-built /
trust-ledger activation path).

---

## 2026-08-09 — MVP-073f: Builder UI — structured form (stage 5b)

**Branch:** `feature/mvp-073f-builder-ui` (off main). **Merge:** `70f2049`. The owner-facing workflow
builder, in `web/` (the store-owner console). Founder chose the **structured form** editor over a
drag-and-drop flow-graph (DECISIONS 2026-08-09); the flow-graph is noted as a future *selectable* view,
cheap to add because the DSL + authoring API (MVP-073e) is the fixed contract. Frontend only, no dep.

- `web/src/lib/workflows.ts` — **pure `composeDsl(draft)`** (owner draft → workflow DSL) +
  `emptyStep` / `validKey`, deliberately decoupled from the editor UI so a graph surface can target the
  same model. Owner step palette: `agent_task` / `wait` / `human_task` / `set` (`emit`/`branch`/`loop`
  excluded — owners can't emit platform events; branch/loop a follow-on).
- `web/src/api.ts` — `validateDefinition` (server-truth), `createDefinition`, `listOwnerDefinitions`.
- `web/src/components/WorkflowsSection.tsx` — list owner drafts + a step-list form with **Validate**
  (round-trips to `/definitions/validate`, shows the server verdict + locked guards) and **Save draft**.
- Nav: **Automations** item + `/workflows` route, gated `catalog:write` (owner+manager; staff/viewer
  don't see it). Frontend gating is UX-only; the backend enforces every call.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Draft → DSL composition (trigger/condition/steps) | `web workflows.test.ts` `composeDsl` (4) | PASS |
| Empty-step defaults + key validation | `web workflows.test.ts` `emptyStep`/`validKey` (2) | PASS |
| Nav gates Automations to catalog:write (owner+manager) | `web roles.test.ts` `visibleNav` (updated) | PASS |

**Web gate:** oxlint clean (2 pre-existing warnings) · `tsc -b --noEmit` clean · **vitest 56** (+6) ·
`npm run build` OK · **guards 0** (industry-nouns scans web/ — none). Backend unchanged.

**Next recommended action:** merge + push + record hash + verify CI, then **stage 6: owner-built /
trust-ledger activation path** — activation of an owner-built draft gated on a simulation report +
tier-2 approval, runs start tier-2-everything until the trust ledger clears ~50 clean runs, then earn
autonomy. That closes the 6-stage workflow initiative; then the Option-A ghost-recovery diagnosis
extension + jewelry pack + eval + CAPTURE-GAP migrations.

---

## 2026-08-09 — MVP-073g: Owner-built activation + trust ledger (stage 6 — CLOSES the workflow engine)

**Branch:** `feature/mvp-073g-owner-trust` (off main). **Merge:** `1fdfed1`. The governance that makes
owner-authored workflows safe to switch on — the final stage of the 6-stage workflow-engine initiative.
No migration, no dependency.

**`core/workflows/activation.py`** — owner-built drafts cannot self-activate. `request_activation` runs
a **simulation** (MVP-073d) and raises a **tier-2 `workflow.activate` approval** with the report
attached; the draft stays a draft until the approval resolves. A new `approval.resolved.v1` consumer
group (`workflow-activation`) calls `apply_activation_decision` → **approve activates** the draft,
**reject leaves it a draft**. **Trust ledger:** `owner_trust_status` counts clean (completed) runs for
the definition; at `TRUST_THRESHOLD = 50` it is `earned` and the `tier_floor` drops from 2 to none —
owner-built runs sit at a max-approval floor until they earn autonomy (surfaced for the mediation
boundary; a draft never routes, so nothing runs before activation anyway).

**API** (`catalog:write`): `POST /v1/workflows/definitions/{id}/activate` (returns approval id +
simulation + trust), `GET /v1/workflows/definitions/{id}/trust`.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Trust earns at the threshold (tier floor drops) | `test_trust_earns_at_threshold` | PASS |
| Request → tier-2 approval + report attached, **stays draft** | `test_request_activation_raises_tier2_approval_and_stays_draft` | PASS |
| Approve activates the draft | `test_apply_decision_activates_on_approve` | PASS |
| Reject leaves it a draft | `test_apply_decision_reject_leaves_draft` | PASS |
| Already-active → rejected | `test_activation_rejected_for_already_active` | PASS |
| Trust counts only completed runs | `test_trust_status_counts_completed_runs` | PASS |

**Commands:** ruff clean · `mypy core` **164** · **guards 0** · **419** unit+isolation · **465**
integ+e2e+contract (+6) · no migration/dep.

**WORKFLOW ENGINE COMPLETE** (6 stages, all merged + CI-green): 1 spine (073a) → 2 waits (073b) →
3 saga/human_task/timeline (073c) → 4 simulation (073d) → 5 builder [backend 073e + UI 073f] →
6 owner-built/trust (073g). **Next:** the **Option-A ghost-recovery diagnosis extension** — the readable
sugar verbs (`diagnose`/`classify_ghost`/`approval_gate`/`compose`) desugared to the generic grammar +
the jewelry pack (8-reason taxonomy, frontier diagnosis prompt, reason-conditioned templates) + the eval
harness (offline/synthetic, real-ready via the gate) + the CAPTURE-GAP migrations for live diagnosis.

---

## 2026-08-09 — MVP-073h: Ghost-recovery diagnosis extension — Option A (sugar → generic grammar)

**Branch:** `feature/mvp-073h-diagnosis-sugar` (off main). **Merge:** `bcea001`. The first of the three
ghost-recovery items (the MVP thesis): the **Option-A readable sugar** that lets a pack author
`diagnose`/`approval_gate`/etc. while the engine stays generic. No migration, no dependency.

**Parser desugar** (`core/workflows/parser.py::desugar`, runs before validation): `diagnose` /
`classify_ghost` / `compose` → `agent_task` (output bound under the verb name via `output_as`);
`approval_gate` → a ranked `human_task` (`allow_owner_handle` → `allow_decline`). Recurses into
branch/loop/compensation. The executor never gains a step type — `core/` stays industry-neutral (guard
clean). **Generic engine additions** (schema + program + executor): `agent_task` gains **`tier`**
(frontier/standard/nano routing, passed to the runtime) and **structured `output` binding** — the
agent's JSON output is stored in vars under `output_as`, narrowed to the declared `output` keys, so a
later branch routes on `diagnose.top_reason`; `human_task` gains a **`ranked` mode** whose approval
payload carries the resolved `options` (from `diagnose.ranked`), `recommended`, and `label_sink`.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Sugar desugars (diagnose/approval_gate/compose, incl. nested) | `test_workflow_sugar.py` (5) | PASS |
| Generic verbs untouched | `test_generic_verbs_are_left_untouched` | PASS |
| **Runtime: agent output binds + a branch routes on it** | `test_diagnose_output_binds_and_routes_the_branch` | PASS |
| **approval_gate parks with a ranked payload (options/recommended/label_sink)** | `test_approval_gate_parks_with_ranked_payload` | PASS |
| silent_lead_reactivation v3 still rejected (beyond extended grammar) | `test_proposed_workflow_with_ungrammar_verbs_is_rejected` (updated) | PASS |

**Commands:** ruff clean · `mypy core` **164** · **guards 0** (core industry-neutral) · **424**
unit+isolation · **467** integ+e2e+contract (+7) · no migration/dep.

**Next recommended action:** merge + push + record hash + verify CI, then item 2 — the **jewelry
ghost-recovery pack** (the 8-reason taxonomy, the frontier diagnosis prompt, the reason-conditioned
templates as declarative `verticals/jewelry/` config + a clean `silent_lead_reactivation` rewrite using
the sugar) — then item 3, the **eval harness** (offline/synthetic, real-ready via the gate) + the
**CAPTURE-GAP migrations** for live diagnosis. Then the **synthetic-data demo** (founder request).

---

## 2026-08-09 — MVP-073i: Jewelry ghost-recovery pack (diagnosis item 2)

**Branch:** `feature/mvp-073i-ghost-pack` (off main). **Merge:** `3cb8906`. The L1 declarative pack that
turns the ghost-recovery thesis into a runnable workflow — no `core/` code (industry nouns live in
`verticals/`, which the guard does not scan). No migration, no dependency.

**New pack config (`verticals/jewelry/`):**
- `playbooks/ghost_reason_taxonomy.yaml` — the 8 frozen reasons, each → a distinct recovery action;
  band-dependent reasons (sticker_shock / comparison_shopping / authenticity_buyback_trust) → high-band
  `act_sales_handoff`; abstain → `act_abstain_owner_pick`, fallback `act_generic_nudge`.
- `templates/recovery.yaml` — 9 reason-conditioned templates (romanized Telugu, `language_profile`
  each). **Committed-figures rule enforced**: no literal figure — every number is a `{{ledger.*}}` /
  `{{piece.*}}` placeholder.
- `prompts/ghost_diagnosis.md` — the frontier diagnosis prompt (ranked over the 8, abstain path,
  evidence spans, never writes a figure).
- `workflows/silent_lead_reactivation.yaml` v3→**v4**: rewritten to the frozen grammar + Option-A sugar
  so it **parses, compiles, and seeds** (was skipped as unparseable). diagnose → approval_gate(ranked,
  reads `diagnose.ranked`, label_sink `lead_diagnoses`) → compose → wait(reply) → branch(reply → emit
  `lead.reengaged`). Block-style guards keep `touch_cap(3, 30d)` intact. classify_ghost + the 24h
  post-quote silence window + the sales-handoff branch are deferred to the CAPTURE-GAP migrations.

**Requirement → evidence** (`tests/unit/test_jewelry_ghost_recovery.py`, rigorous corner cases):
| Criterion | Test | Result |
|---|---|---|
| Exactly the 8 frozen reasons; each has an action | `test_taxonomy_has_exactly_the_eight_frozen_reasons`, `test_every_reason_has_an_action` | PASS |
| Band-dependent reasons hand off at high band (and only those) | `test_band_dependent_reasons_hand_off_at_high_band` | PASS |
| Every referenced action has a template; no-template actions don't | `test_every_customer_action_has_a_template` | PASS |
| **No orphan templates** | `test_no_orphan_templates` | PASS |
| **No literal figure in any template** (committed-figures rule) | `test_no_template_contains_a_literal_figure` | PASS |
| Prompt names all 8 reasons + abstain + frontier + no-figure | `test_prompt_names_all_eight_reasons_and_the_guardrails` | PASS |
| v4 workflow parses + routes on `diagnose.*` (ranked gate) | `test_workflow_uses_diagnosis_output_and_ranked_gate`, `test_silent_lead_reactivation_v4_parses_via_the_sugar` | PASS |

**Commands:** ruff clean · `mypy core` **164** · **guards 0** · **434** unit+isolation (+14) · **467**
integ+e2e+contract · pack installer + jewelry/kirana e2e green (v4 now seeds). No migration/dep.

**Next recommended action:** merge + push + record hash + verify CI, then item 3 — the **eval harness**
(offline/synthetic ghost set → the gated-simulated diagnosis, would-fire / confusion, real-ready via
`llm_provider_enabled`) + the **CAPTURE-GAP migrations** (quoted_catalog_item_id, is_price_reveal,
first_customer_response, last_outbound_msg_at/direction, `lead_diagnoses`) for live diagnosis. Then the
**synthetic-data demo** (founder request).

---

## 2026-08-09 — MVP-073j: Ghost-diagnosis eval harness + CAPTURE-GAP migrations (diagnosis item 3)

**Branch:** `feature/mvp-073j-eval-capture` (off main). **Merge:** `e52cceb`. Closes the three-item
ghost-recovery diagnosis track. Migration 038, no dependency.

**Migration 038 (CAPTURE-GAPs, additive; up/down verified):** the schema LIVE diagnosis needs (the
offline eval/demo need none of it):
- `leads` += `quoted_catalog_item_id` (FK catalog_items, GAP-01), `first_customer_response_at` /
  `first_response_message_id` (GAP-03), `last_outbound_msg_at` / `last_message_direction` (GAP-04);
- `messages` += `is_price_reveal` (GAP-02);
- **`lead_diagnoses`** (GAP-06): stored diagnosis + owner label (RLS-forced) — the `label_sink` the
  recovery approval's owner-pick writes to.

**Eval harness (`scripts/ghost_eval.py`, jewelry logic, not `core/`):** a **gated-simulated**
deterministic keyword diagnoser over the 8 reasons — provider OFF → deterministic ranked output;
provider ON but the frontier model unwired → fail-closed `provider_unavailable`; **real-ready** (flip
the gate + wire the model → the SAME workflow runs on real threads). `run_eval` scores accuracy +
confusion over `verticals/jewelry/playbooks/synthetic_ghost_set.yaml` (18 cases, ≥2/reason + abstain).
`uv run python scripts/ghost_eval.py` → **18/18, accuracy 1.0** (plumbing proven; real correctness
needs the D1/D2 loop). The synthetic set lives in `playbooks/` (not `evals/`, whose pack contract is
the concierge-style `EvalSuite`).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Migration up/down + `lead_diagnoses` RLS + isolation | `alembic up/down`, `tests/isolation/test_lead_diagnoses_rls.py` | PASS |
| Synthetic set covers every reason + abstain | `test_synthetic_set_covers_every_reason_and_abstain` | PASS |
| Accurate + diagonal confusion on the synthetic plumbing | `test_eval_is_accurate_on_the_synthetic_plumbing` | PASS |
| Deterministic; valid reasons/actions; ranked ≈ 1.0 | `test_diagnosis_is_deterministic`, `test_outputs_are_valid_reasons_and_actions` | PASS |
| **Thin thread abstains (not guesses)** | `test_thin_thread_abstains_rather_than_guesses` | PASS |
| **Fail-closed gate when provider enabled** | `test_diagnoser_fails_closed_when_provider_enabled` | PASS |
| recommended_action matches the taxonomy map | `test_recommended_action_matches_the_taxonomy_map` | PASS |

**Commands:** ruff clean · `mypy core` **164** · **guards 0** · **442** unit+isolation (+8) · **467**
integ+e2e+contract · migration 038 up/down. No dependency. (Two transient DB-pool flakes in a mixed
run cleared on re-run — not code.)

**GHOST-RECOVERY DIAGNOSIS TRACK COMPLETE:** Option-A sugar (073h) → jewelry pack (073i) → eval +
CAPTURE-GAPs (073j). **Next: the synthetic-data demo** (founder request) — run the full v4
`silent_lead_reactivation` end to end on the synthetic set through the executor (diagnose → ranked
approval → compose → wait → reengage), offline / $0, showing would-recover counts + the diagnosis loop.

---

## 2026-08-09 — MVP-074: Real LLM provider adapter (gated, real-ready) — priority item 1

**Branch:** `feature/mvp-074-llm-adapter` (off main). **Merge:** `e28c2f5`. First of the founder's
post-demo priorities: make **real diagnosis** possible behind the gate. No migration, **no new
dependency** (httpx is already present — no vendor SDK).

**`core/runtime/llm_client.py`** — the single real-model call. httpx POST to **Anthropic** (`/v1/
messages`, default per CLAUDE.md) or **OpenAI** (`/v1/chat/completions`); the request/parse shape is the
only difference. **Fails closed** (`provider_unavailable`) unless `llm_provider_enabled` AND
`llm_api_key` are set, so the whole system keeps running simulated by default. Wired into the runtime:
`RealModel.turn` and `get_provider` → a real `LlmProvider` (replacing the `NotImplementedError` stub),
so the agent loop can use a real model when enabled (returns the model's text; tool-calling later).

**`scripts/ghost_eval.py`** — `real_diagnose` (pack diagnosis prompt → model → JSON) with the model's
**untrusted output re-validated** against the frozen taxonomy (out-of-taxonomy/malformed → abstain; the
recovery action is always re-derived from the taxonomy, never trusted from the model); a `diagnose()`
dispatcher runs real when enabled, else the simulated diagnoser. The offline eval is unchanged (18/18).

**Config:** `llm_provider` (anthropic|openai), `llm_api_key` (**secret** — env/SOPS, never committed),
`llm_model`, `llm_api_base` (per-provider default), `llm_max_tokens`. All off unless enabled.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Disabled by default → fails closed | `test_disabled_by_default_fails_closed` | PASS |
| Enabled without a key → fails closed | `test_enabled_without_key_fails_closed` | PASS |
| Anthropic request shape + parse (mocked HTTP) | `test_anthropic_request_shape_and_parse` | PASS |
| OpenAI request shape + parse (mocked HTTP) | `test_openai_request_shape_and_parse` | PASS |
| diagnose dispatcher: off → simulated; on+no-key → fail closed | `test_diagnose_*` | PASS |
| **Real diagnosis re-derives the action from the taxonomy** | `test_real_diagnose_parses_json_and_re_derives_the_action` | PASS |
| **Hallucinated reason → abstain** | `test_real_diagnose_abstains_on_out_of_taxonomy_reason` | PASS |

**Commands:** ruff clean · `mypy core` **165** · **guards 0** · **451** unit+isolation (+9) · **463**
integ+e2e+contract · eval `18/18`. No migration, no dependency. Tests never hit the network.

**Enabling for real (founder-gated, costs credits):** set `GROWTH_OPERATOR_LLM_PROVIDER_ENABLED=true`
and `GROWTH_OPERATOR_LLM_API_KEY=…` (Anthropic key; or `_LLM_PROVIDER=openai` + an OpenAI key). Nothing
else changes — the ghost-recovery workflow then diagnoses on the real frontier model.

**Next (priority order):** item 2 — the **notification bell** (aggregate approvals/tickets/workflow
events from the existing event stream) → item 3 — the **WABA send adapter** real-ready behind the gate.

---

## 2026-08-09 — MVP-075: Notification bell (owner feed) — priority item 2

**Branch:** `feature/mvp-075-notification-bell` (off main). **Merge:** `7076621`. Functional-first (UX
polish is a later pass, per founder). Backend + web. Migration 039, no dependency.

**Derived, not a new pipeline:** the feed aggregates signals that already exist — **pending approvals**
(the owner must act; a customer reply won't send until they do), **support-ticket updates**
(in_progress/resolved), and **automation alerts** (workflow runs failed/compensated). Each item: kind,
title, timestamp.

- Migration **039** `notification_reads` (per-user `seen_at`, one row per (org,user), RLS-forced;
  up/down verified). `core/notifications/service.py`: `get_feed` (merge + newest-first; unread = items
  newer than `seen_at`) + `mark_seen` (upsert). `core/notifications/api.py`: `GET /v1/notifications`,
  `POST /v1/notifications/seen` (`insights:read` — every role holds it), registered.
- Web (`web/`): `NotificationBell.tsx` — a 🔔 in the Shell header with an unread badge (caps at 9+) and
  a dropdown feed; opening it marks-seen (clears the badge); react-query polls every 30s.
  `lib/notifications.ts` pure helpers (badge/kind/relative-time), `api.ts` client.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Feed aggregates the 3 signals, newest first | `test_feed_aggregates_the_three_signals` | PASS |
| mark_seen clears; a later signal is unread | `test_mark_seen_clears_then_a_new_signal_is_unread` | PASS |
| `notification_reads` RLS isolation | `tests/isolation/test_notification_reads_rls.py` | PASS |
| Badge cap / kind labels / relative time | `web lib/notifications.test.ts` (3) | PASS |

**Commands:** ruff clean · `mypy core` **168** · **guards 0** · **452** unit+isolation (+2) · **465**
integ+e2e+contract (+2) · migration 039 up/down · web gate (oxlint/tsc/**vitest 59**/build) green. No dep.

**Next:** priority item 3 — the **WABA send adapter** real-ready behind the gate (so real WhatsApp is a
config flip once Meta verification clears), then the bigger multi-channel/advertising + UX tracks.

## 2026-08-10 — MVP-076: WABA send adapter real-ready — live-path test coverage — priority item 3

**Branch:** `feature/mvp-076-waba-adapter` (off main). **Merge:** `981ad19`. Tests only; **no `core/`
change**.

**Honest finding:** the real Meta Graph-API send path was **already built** (MVP-031/034) — `MetaClient`
(`core/channels/whatsapp/meta_client.py`) has real httpx paths for send_text/send_template/verify/webhook/
templates, gated by `whatsapp_live_enabled` (simulated when off), wrapped by the 5-gate `send()`
(`core/channels/whatsapp/send.py`: audit-cap → execution-token → suppression → consent → figures-ledgered,
plus bounded 429/5xx retries). The **one gap** for "real-ready": every existing test ran the *simulated*
branch, so the **live request shape + response parsing were never verified** — a wrong payload/header would
have surfaced only against a real Meta account at go-live. This ticket closes that with **no network, no
real Meta account, no real send** (§10.4).

- `tests/unit/test_meta_client_live.py` (**+6**): flip `whatsapp_live_enabled=true`, mock
  `httpx.AsyncClient.post`/`.get`, and pin — `send_text` URL `/{phone_number_id}/messages`, `Authorization:
  Bearer …`, body `{messaging_product:whatsapp, to, type:text, text:{body}}` → `wamid` parsed; `send_template`
  body `{type:template, template:{name, language:{code}}}`; **429** → `retry_after_s` from `Retry-After`,
  `ok=False`; **5xx** → `ok=False` + error; `verify_credentials` GET + bearer; and a default-off sanity check
  that the simulated path makes **no network call** and returns a `wamid.SIM-` id.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Live text send: real request shape + wamid parse | `test_send_text_live_request_shape_and_parse` | PASS |
| Live template send: real request shape | `test_send_template_live_request_shape` | PASS |
| 429 surfaces Retry-After, fails | `test_send_live_429_surfaces_retry_after` | PASS |
| 5xx fails with status + error | `test_send_live_5xx_is_failed` | PASS |
| Live credential verify (bearer) | `test_verify_credentials_live` | PASS |
| Default-off = simulated, no network | `test_simulated_by_default_no_network` | PASS |

**Go-live path (for later, needs founder + Meta approval):** set `GROWTH_OPERATOR_WHATSAPP_LIVE_ENABLED=true`
**and** connect a real number via `POST /v1/channels/whatsapp/connect` (stores real `phone_number_id` +
Fernet-encrypted `access_token` per org). Sends still require an **approval + execution token** (unchanged).
External blocker remains: Meta WABA verification (BLOCKERS #3) — code is ready; no credentials wired.

**Commands:** ruff clean · `mypy core` **168** · **guards 0** · **441** unit (+6) · whatsapp integ
(send/connect/templates/media/figure-check) **31 passed, 4 skipped** (DB-gated). No dep, no migration, no
external call.

**Next:** the bigger multi-channel/advertising tracks (email, Instagram, Google Ads) + the UX pass — both
captured in VISION_INTAKE.

## 2026-08-10 — UX-00: vendor curated UI/UX design skills — merge `edc760c` (+ cleanup `0455e7a`)

Founder asked to install four open-source design-skill collections so Claude Code has strong UI/UX
craft. Installed **11, markdown-only** (guidance; zero executable surface) under `.claude/skills/`:
`impeccable` (Apache-2.0, premium-craft playbooks — scripts/live-browser/image-gen toolchain omitted),
`apple-design`/`animate`/`animation-vocabulary`/`review-animations`/`emil-design-eng` (MIT, Emil
Kowalski), `design-system`/`ui-styling`/`brand` (MIT, nextlevelbuilder), `design-taste-frontend`/
`redesign-existing-projects` (MIT, leonxlnx). Security-scanned all SKILL.md + scripts (clean). Sources,
licenses, and held-back menu in `.claude/skills/README.md`. Cleanup `0455e7a` stripped orphaned
`ui-styling/canvas-fonts/*-OFL.txt` + empty `scripts/`. No product code touched.

## 2026-08-10 — UX-01: design foundation + Shell + Login + bell (stage U1 of the UX pass)

**Branch:** `feature/ux-01-foundation-shell-login`. **Direction v2 "Atelier"** (founder-approved via
mockup): committed emerald accent on a cool porcelain ground, ink w/ green undertone, serif reserved for
money/wordmark, drawn icons (no emoji), themed browser surfaces. Purely presentational — no API/route/
data change. Applied the installed **craft-floor** (dropped eyebrow kickers, the 4-stat template, emoji
icons; themed selection/scrollbar/focus/caret) + **new-work** (Operate surface → premium via precision +
one committed accent) skills.

- `web/src/index.css`: full **token system** via Tailwind v4 `@theme inline` over CSS custom properties
  (light default + `prefers-color-scheme: dark` + `data-theme` override, both directions) → theme-reactive
  utilities (`bg-surface`/`text-ink`/`text-accent`/`border-line`/`shadow-card`/`font-serif`…). Base layer
  themes selection, scrollbar, focus-visible, caret.
- `web/src/components/icons.tsx` (**new**): drawn SVG icon set, one 1.6 stroke weight (Mark monogram,
  Bell, Check/CheckCircle, ArrowRight, SignOut, Ticket, Gear) — replaces all emoji.
- `Shell.tsx`: re-skinned header — monogram mark, emerald active-underline nav (same permission-gated
  links/order, so `roles.test.ts` visibleNav is untouched), avatar + role, drawn sign-out.
- `Login.tsx`: re-skinned passwordless sign-in (serif wordmark, emerald wash, themed caret/focus, drawn
  arrow); dropped the always-visible dev-OTP hint (not production-appropriate).
- `NotificationBell.tsx`: drawn Bell + per-kind drawn icons (approval/ticket/automation) replacing emoji;
  restyled dropdown on tokens. Feed/mark-seen/poll logic unchanged.

**Verify:** oxlint clean (2 pre-existing warnings) · `tsc -b` 0 · **vitest 59** · `vite build` ✓ ·
**guards 0** (emerald/porcelain/ink are not industry nouns) · no forbidden nouns in changed files.
**Next stages:** U2 dashboard + shared primitives → U3 work surfaces → U4 web-ops.

## 2026-08-10 — UX-02: dashboard recompose + shared primitives (stage U2)

**Branch:** `feature/ux-02-dashboard-primitives`. Presentational only — same endpoints
(`/v1/dashboard/overview` + insights summary), no new data. Applied the craft-floor: replaced the
**four-identical-stat-card template** with real hierarchy grounded in real numbers.

- **Shared primitives** (used across U3): `web/src/lib/ui.ts` — pure class helpers (`buttonClasses`,
  `tagClasses`, `cardClasses`; lint-safe, non-component); `web/src/components/ui.tsx` — `Card`,
  `PageHeader`, `Stat`, `Tag`, `Button`, `EmptyState`, `CaretLink`. `icons.tsx` gained
  `MessageCircle`/`Grid`.
- **Dashboard** (`HomeSection.tsx`) recomposed: **approvals lead** as the focal "Waiting for your OK"
  panel (the owner's core daily act — nothing sends until approved), with a calm all-caught-up state at
  zero; **this-week revenue** as the proof card (real delta vs last week + leads/orders, no fake
  sparkline); a subordinate **quick-links** row (conversations/catalog/tickets) with drawn icons. Themed
  skeleton + error states.
- Removed superseded `lib/home.ts` + `lib/home.test.ts` (the old `HOME_TILES` config, now replaced by
  the inline quick-links) — dead-code cleanup, so **vitest 59 → 57**.

**Verify:** oxlint clean (2 pre-existing) · `tsc -b` 0 · **vitest 57** · `vite build` ✓ · **guards 0** ·
no forbidden nouns. **Next:** U3 work surfaces (approvals/conversations/campaigns/…) on these primitives.

## 2026-08-10 — UX-03a: Approvals + Conversations onto primitives (stage U3a)

**Branch:** `feature/ux-03a-approvals-conversations`. Presentational only — all mutations/queries/
permission-gating preserved. `ApprovalsSection` (view/edit/reject modes, tier badges, matched-rules,
resolve mutation) and `ConversationsSection` (inbox↔thread, leads pipeline, tabs) re-skinned on the
tokens + `Card`/`PageHeader`/`EmptyState`/`Tag`. Added `danger`/`danger-ghost` button variants
(`lib/ui.ts`). Harmonized `lib/leads.ts` `STAGE_STYLE` from rainbow pastels to token tones (won=good,
quoted=warn, lost=danger; leads.test doesn't pin the strings). Chat bubbles: store=ink/porcelain,
customer=porcelain/ink. **Verify:** oxlint clean · tsc 0 · **vitest 57** · build ✓ · guards 0.

## 2026-08-10 — UX-03b: Campaigns + Customers + Catalog (stage U3b)

**Branch:** `feature/ux-03b-campaigns-customers-catalog`. Presentational only — all queries/mutations/
permission gating + the campaign typed-count-confirm flow preserved. Re-skinned on tokens + primitives;
added `fieldClasses` (shared form-field helper) + `Megaphone`/`Users`/`Box`/`Plus` icons. Harmonized
`lib/customers.ts` `CONSENT_STYLE` and `lib/catalog.ts` `AVAILABILITY_STYLE` (+ campaigns status tones) to
token tones (good/warn/danger/muted; no test pins the strings). Shape-only `Badge` so the tone class
doesn't collide. **Verify:** oxlint clean · tsc 0 · **vitest 57** · build ✓ · guards 0.

## 2026-08-10 — UX-03c: Insights + Automations (stage U3c)

**Branch:** `feature/ux-03c-insights-automations`. Presentational only — the escalating question-levels,
the "Ask Growth Operator" thread, and the owner workflow builder/validate/save flow all preserved.
`InsightsSection` (TONE_DOT/TONE_BADGE → token tones) and `WorkflowsSection` (STATUS tones, `input`/`btn`
→ `fieldClasses`/`buttonClasses`) re-skinned on tokens + primitives; `BarChart`/`Bolt` icons added.
**Verify:** oxlint clean · tsc 0 · **vitest 57** · build ✓ · guards 0.

## 2026-08-10 — UX-03d: Support + Team + Settings + shell states (stage U3d — tenant app COMPLETE)

**Branch:** `feature/ux-03d-support-team-settings`. Presentational only. Re-skinned `SupportSection`
(status/priority/severity tones → tokens), `TeamSection`, `SettingsSection` (local Card → `SettingCard`
over the primitive; 🔒 emoji → drawn `Lock`; Auto/Review + pause toggle on tokens), plus the shared
`ComingSoon`, `ErrorBoundary`, and `main.tsx` loading/no-org shells. Added `Lock`/`LifeBuoy` icons.
**Full-app sweep:** zero legacy `neutral-*`/`bg-white`/color-scale classes remain in `web/src` — the
**entire tenant app is on the design tokens.** **Verify:** oxlint clean · tsc 0 · **vitest 57** · build ✓
· guards 0. **Next:** U4 — `web-ops` operator console gets the same system.

## 2026-08-10 — UX-04: operator console (web-ops) on a dark control-plane theme (stage U4 — UX PASS COMPLETE)

**Branch:** `feature/ux-04-web-ops-console`. Presentational only — all operator queries/mutations/
permission gating preserved. Gave `web-ops` its **own dark control-plane token system** (same token
*names* as the tenant app, so the shared primitives work; deep graphite ground, on-brand emerald accent,
serif wordmark, single committed dark theme — reads as an internal instrument).

- `web-ops/src/index.css`: dark token system (`@theme inline`, themed selection/scrollbar/focus).
- Copied the shared foundation into web-ops: `components/icons.tsx`, `lib/ui.ts`, `components/ui.tsx`.
- Re-skinned the frame (`Shell` with monogram + emerald active-underline nav, `Login`, `Placeholder`,
  `PlaneDisabled`, `main.tsx` loading) and **all 7 sections** (Queue, Stores, Operational, Analytics,
  CustomerSuccess, Financial, StoreReports) — status/priority/severity/tone maps → token tones; local
  `Card` clashes renamed (`MetricCard`/`StatCell`); forms → `fieldClasses`/`buttonClasses`.
- **Sweep:** zero legacy `slate-*`/`indigo-*`/color-scale classes remain in `web-ops/src`.

**Verify:** oxlint clean · `tsc -b` 0 · **vitest 6** · `vite build` ✓ · guards 0 (web-ops not
noun-scanned by the guard, but manually clean). **UX pass complete** — both apps fully on the design
system. **Next track:** multi-channel/advertising, then one channel end-to-end (per founder order).

## 2026-08-10 — UX-05: warm cream/gold re-theme, both apps (feedback B)

**Branch:** `feature/ux-05-cream-gold`. Founder found the emerald/dark-green gloomy and asked for a
**light cream + antique-champagne** look on **both** apps. Presentational only — token values only.

- `web/src/index.css` re-themed to the cream/champagne token system (light default + `prefers-color-scheme`
  dark + `data-theme` override); **`web-ops/src/index.css` set to the identical palette** (both apps unified
  — no more dark control-plane; the operator app is distinguished by content + the "Operator console"
  wordmark).
- Added an **`--on-accent`** token (fixed dark tone) → gold is light in both themes, so
  `buttonClasses.primary` and badges now use `text-on-accent` (dark on gold) to stay AA-legible instead of
  white-on-gold. Fixed the same in NotificationBell badge, ErrorBoundary, both Login primaries, and both
  Shell/Login marks (`bg-ink` mark for contrast).
- Comments avoid the literal industry noun (accent described as "champagne"; token named `--accent`).

**Verify:** web + web-ops each — oxlint clean · `tsc -b` 0 · vitest (web 57 / web-ops 6) · `vite build` ✓ ·
**guards 0** · no forbidden nouns in either app (incl. `.css`). First ticket of the Operator-Console-v2
backlog (`project-management/OPERATOR_V2_BACKLOG.md`). **Next:** OC1 — editable plans + "what's included".

## 2026-08-10 — OC1: editable plans + "what's included" (feedback A)

**Branch:** `feature/oc1-editable-plans`. Founder couldn't edit created plans and wanted to see what each
plan includes. Plans previously stored only name + price with create/list only.

- **Migration `0855d6b58a71`** (on `bdaf25315e59`): `billing_plans` gains `description text` +
  `features jsonb NOT NULL DEFAULT '[]'`. Global GO table (no RLS). **Up/down/up verified** on live PG
  (columns added → dropped → re-added).
- **Backend:** `core/billing/service.py` — `_PLAN_COLS` + `create_plan` extended; new `update_plan`
  (RETURNING → `None` when the id is unknown). `core/billing/api.py` — `PlanCreate`/`PlanOut` gain
  description/features; new `PlanUpdate`; **`PATCH /v1/admin/billing/plans/{id}`** (`PLATFORM_TENANTS_MANAGE`),
  **404** unknown id, **409** duplicate name (IntegrityError → HTTPException), audited `billing.plan.updated`.
- **Frontend (web-ops):** `api.ts` — `BillingPlan` gains description/features, new `PlanInput` +
  `adminUpdatePlan`, `adminCreatePlan` takes optional description/features. `lib/plans.ts` (parse/format
  features, rupeesToMinor) + `plans.test.ts`. `FinancialSection` Plans panel rebuilt: each plan shows its
  "what's included" list (drawn Check bullets); inline **edit** form (name/price/active/description/features)
  + a New-plan form.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Create with features + edit price/desc/features/active, round-trips | `test_create_and_edit_plan_features` | PASS |
| Edit unknown id → 404 | `test_edit_plan_404_for_unknown_id` | PASS |
| Edit onto an existing name → 409 | `test_edit_plan_409_on_duplicate_name` | PASS |
| Edit as non-operator → 403 | `test_edit_plan_403_for_non_operator` | PASS |
| Feature editor parse/format | web-ops `lib/plans.test.ts` (4) | PASS |

**Commands:** ruff clean · `mypy core` 168 · guards 0 · migration up/down/up · **billing integ 9** (+4) ·
**unit+contract 438** · web-ops oxlint/tsc/**vitest 10**(+4)/build ✓. No secrets, no external calls, no
tenant-isolation change (global admin-plane-only table). **Next:** OC2 — per-store spend-by-channel + ROI.

## 2026-08-10 — OC2: per-store spend-by-channel (feedback D)

**Branch:** `feature/oc2-spend-by-channel`. Founder wanted to see, per store, where the money goes
(WhatsApp / Instagram / SEO / Google Ads …). The `billing_charges` spine already holds per-store/-month
amount + our-cost per `charge_type`; OC2 adds channel granularity + a visible breakdown.

- **Migration `c84cf2817c98`** (on `0855d6b58a71`): widen `billing_charges_charge_type_check` to add
  **`whatsapp`, `instagram`, `google_ads`** (keeps the existing values → additive, rows preserved).
  **Up/down/up verified** on live PG (constraint gains/loses `whatsapp`). Downgrade fails if a row already
  uses a new value (documented — reclassify first).
- **Backend:** `ChargeType` literal widened (record-charge validates against it).
- **Frontend (web-ops):** `lib/spend.ts` (`spendByChannel` grouping → per-channel amount/cost/margin +
  totals, sorted; `channelLabel`) + `spend.test.ts`. `FinancialSection` gains a **"Where the money went ·
  by channel"** panel (bars + margin per channel + totals) on the tenant billing view; the record-charge
  dropdown offers the new channels; raw list relabelled "Charges · detail". **Decision:** treated
  `charge_type` as the channel (expanded the enum) rather than a separate column. ROI-per-channel deferred
  (needs per-channel attribution — belongs to OC4/analytics).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| New channel charge types accepted + stored | `test_charge_accepts_new_channel_types` | PASS |
| Group by channel: amount/cost/margin + totals, sorted | web-ops `lib/spend.test.ts` (grouping) | PASS |
| Empty charges → empty breakdown / zero totals | web-ops `lib/spend.test.ts` (empty) | PASS |
| Channel labels + fallback | web-ops `lib/spend.test.ts` (labels) | PASS |

**Commands:** ruff clean · `mypy core` 168 · guards 0 · migration up/down/up · **billing integ 10** (+1) ·
**unit+contract 438** · web-ops oxlint/tsc/**vitest 13**(+3)/build ✓. No secrets, no external calls, no
tenant-isolation change. Also ticketed **TX1** (Automations onboarding examples) + **PAY0–PAY3** (Razorpay
charge + WhatsApp/email receipt) from new founder feedback. **Next:** OC3 — plan-aware ticket priority + SLA.

## 2026-08-10 — TX1: Automations onboarding — worked examples + option docs (feedback, tenant app)

**Branch:** `feature/tx1-automation-examples`. Founder found the tenant Automations page hard to operate.
Presentational only — no API/backend change; the server still validates + reviews every draft.

- `web/src/lib/automationExamples.ts` (+ `.test.ts`): **7 ready-made examples** (2 simple / 2 medium /
  3 complex) as valid `WorkflowDraft`s the builder loads directly (welcome enquiry, quote follow-up,
  reactivate-quiet, festival greeting, **ghost-recovery**, high-value concierge, post-purchase review);
  plus **`AUTOMATION_OPTIONS`** — for every option (trigger event, condition/CEL, the 4 owner step types,
  and the server-locked guards) a plain-language **what / why / how**, like documented script arguments.
- `web/src/components/WorkflowsSection.tsx`: a **"Start from an example"** gallery (grouped by complexity;
  "Use this" loads the draft into the builder + scrolls to it) + a collapsible **"How automations work —
  the options"** reference panel. `Builder` gained an `initialDraft` prop; the section reseeds it via a
  remount key.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| ≥2 simple / ≥2 medium / ≥3 complex examples | `automationExamples.test` (spread) | PASS |
| Every example is a valid, editable, composable draft | `automationExamples.test` (valid draft) | PASS |
| Unique ids + workflow keys | `automationExamples.test` (unique) | PASS |
| Every option doc has what/why/how | `automationExamples.test` (options) | PASS |

**Commands:** oxlint clean (2 pre-existing) · `tsc -b` 0 · **vitest 61** (+4) · `vite build` ✓ · guards 0 ·
no forbidden nouns. **Next:** OC3 — plan-aware ticket priority + SLA.

## 2026-08-10 — OC3: plan-aware ticket priority + SLA (feedback C)

**Branch:** `feature/oc3-plan-aware-tickets`. **Frontend-only** — no migration/backend. The roster already
exposes each store's plan via `platform_tenant_roster()`, and the admin session can't read the RLS'd
`billing_subscriptions` directly (that's why billing uses SECDEF), so the Queue joins **tickets ↔ roster
(org→plan) ↔ plan catalog (name→price for tier rank)** client-side — cleaner than a new SECDEF.

- `web-ops/src/lib/ticketPriority.ts` (+`.test.ts`): `plansByTier` (price desc → tier order), `tierRank`,
  `slaHoursForTier`, `slaStatus` (breach + compact label), `rankTickets` (open→closed, breached first,
  higher tier, then priority/severity/age). **SLA defaults (tunable):** top plan **4h**, next **8h**, next
  **24h**, base/none **48h** (`SLA_HOURS_BY_TIER`).
- `web-ops/src/components/QueueSection.tsx`: fetches tenants + plans alongside tickets (**degrades
  gracefully** without `tenants:read` → urgency-only sort); each row shows a **plan badge** + **SLA
  countdown**, breached rows highlighted red; header shows the breached count; **tier-aware sort**.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Plans → tier order; tierRank + fallback | `ticketPriority.test` (plansByTier/tierRank) | PASS |
| SLA hours per tier, clamped | `ticketPriority.test` (slaHoursForTier) | PASS |
| SLA breach at the boundary + labels | `ticketPriority.test` (slaStatus) | PASS |
| Rank: open→closed, breached, tier, urgency | `ticketPriority.test` (ranks) | PASS |

**Commands:** web-ops oxlint clean · `tsc -b` 0 · **vitest 17** (+4) · `vite build` ✓ · guards 0 · no
forbidden nouns. **SLA numbers are defaults — flag for founder to tune.** **Next:** OC4 — Tenant 360.

## 2026-08-10 — OC4: Tenant 360 performance profile (feedback E)

**Branch:** `feature/oc4-tenant-360`. Founder chose the full version incl. the per-store revenue trend.
Clicking a store → a profile combining performance + spend (OC2) + plan (OC1) + priority tickets (OC3) +
insight reports.

- **Migration `b6123061f10b`** (on `c84cf2817c98`): `platform_store_analytics(p_org, p_days)` —
  **SECURITY DEFINER**, org-scoped, same curated pattern as the all-stores rollup (031). One row of
  SUMS/COUNTS for ONE store, current window + prior (for the trend); never customer rows/PII; scoped to
  the org passed in so the admin flag isn't widened. **Up/down verified** (function drops/recreates).
- **Backend:** `core/tenancy/tenants_admin.py` — `StoreAnalytics` + **`GET /v1/admin/tenants/{org}/analytics`**
  (`platform.tenants:read`, audited). Test proves **cross-store isolation** (org A's revenue never leaks
  into org B's rollup) + 403/401/404 gates.
- **Frontend (web-ops):** `api.ts` `StoreAnalytics` + `adminStoreAnalytics`; `lib/analytics.ts`
  (`wowDelta`/`rupees`, +test). **`StoreReportsSection` → a Tenant 360 profile**: header (name/status/plan
  + features), **performance strip** (revenue trend + orders/leads/quotes + campaign line), **spend-by-
  channel** (OC2 lib), **priority tickets** (OC3 lib, top 6 w/ SLA), and the existing **insight reports**.
  Each panel gated per-permission, degrades gracefully.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Per-store rollup moves by that store's numbers | `test_per_store_rollup_isolated_between_stores` | PASS |
| One store's numbers never leak into another | same (org A vs org B) | PASS |
| Gates: 403 non-op / 401 no token / 404 plane off | `test_store_analytics_{403,401,404…}` | PASS |
| WoW delta up/down/flat + zero prior | web-ops `lib/analytics.test` | PASS |

**Commands:** ruff clean · `mypy core` 168 · guards 0 · migration up/down/up · **admin integ 13** (tenants
+analytics+store-analytics, +4) · **unit+contract 438** · web-ops oxlint/tsc/**vitest 20**(+3)/build ✓. No
secrets; the SECDEF returns aggregate-only, org-scoped, no PII. **Completes the founder's TX1→OC3→OC4 set.**
**Remaining backlog:** PAY0–PAY3 (Razorpay + receipts), OC5–OC12 forecast, SLA-number tuning.

## 2026-08-10 — PAY0: gated email channel adapter (SMTP, simulated by default)

**Branch:** `feature/pay0-email-adapter`. Backend only — no migration, **no new dependency**. First step
of the PAY (Razorpay charge → receipt) track; also the parked multi-channel email adapter. Founder chose
**email over SMTP** (open standard, provider-agnostic), free/self-hosted providers (DECISIONS 2026-08-10).

- `core/channels/email/__init__.py`: `EmailClient.send(to, subject, text, html=None) -> EmailResult`.
  **Gated + simulated by default** (`email_live_enabled=False` → fake id, no network). Real path = SMTP
  STARTTLS via stdlib `smtplib` (reuses the `smtp_*` config from OTP email), run in a thread; enabled but
  unwired (`smtp_host`/`smtp_from`) → `provider_unavailable`; SMTP errors → failed result, not a crash.
  Never sends without the gate + an approved action (§10.4). Provider-agnostic: Mailpit (dev) / Postal /
  free-tier relay (prod).
- `core/common/config.py`: `email_live_enabled` (default False), separate from `otp_email_enabled`.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Default-off = simulated, **no network** | `test_simulated_by_default_no_network` | PASS |
| Live but unwired → provider_unavailable | `test_live_but_unwired_fails_closed` | PASS |
| Live path sends over SMTP (mocked); correct headers | `test_live_path_sends_over_smtp` | PASS |
| SMTP error → failed result, not a crash | `test_live_smtp_error_is_failed_result` | PASS |

**Commands:** ruff clean · `mypy core` 169 · guards 0 · **unit 439** (+4) · no external call, no new dep,
no secrets (SMTP password never logged). Decision recorded in `DECISIONS.md`. **Next (PAY track):** PAY1
Razorpay charge adapter (gated) → PAY2 receipt generation → PAY3 deliver to WhatsApp + email.

## 2026-08-10 — PAY1: gated Razorpay payment adapter (payment-links + webhook)

**Branch:** `feature/pay1-razorpay-adapter`. Backend only — no migration, **no new dependency** (httpx).
Payment-links + webhook capture, the cross-industry standard (DECISIONS 2026-08-10). Fully **simulated**
until real keys — no money moves without the gate + an approved action (§10.4).

- `core/payments/razorpay.py`: `RazorpayClient.create_payment_link(amount_minor, description, contact…)`
  → **simulated by default** (fake `plink_SIM…` + short_url, no network); real path = `POST /v1/payment_links`
  (HTTP Basic auth, amount in paise = our minor units, INR); enabled-but-keyless → `provider_unavailable`;
  non-2xx → failed result. `verify_webhook_signature(body, sig)` → HMAC-SHA256 (constant-time), **fails
  closed** on missing secret/sig — a spoofed "paid" callback is rejected.
- `core/common/config.py`: `razorpay_live_enabled` (default False) + `razorpay_key_id` /
  `razorpay_key_secret` / `razorpay_webhook_secret` (secrets, empty by default).

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Default-off = simulated, no network, no charge | `test_simulated_by_default_no_network` | PASS |
| Live but keyless → provider_unavailable | `test_live_but_keyless_fails_closed` | PASS |
| Live create-link request shape (mocked) | `test_live_create_payment_link_request_shape` | PASS |
| Non-2xx → failed result | `test_live_error_returns_failed` | PASS |
| Webhook sig: valid accept / spoof + missing + tampered reject | `test_verify_webhook_signature` | PASS |

**Commands:** ruff clean · `mypy core` 171 · guards 0 · **unit 444** (+5) · scaffold imports clean · no
external call, no new dep, no secrets logged. Decision in `DECISIONS.md`. **Next:** PAY2 — receipt
generation (Shopify-style) → PAY3 — deliver the receipt to WhatsApp + email (uses PAY0 + WABA send).

## 2026-08-10 — PAY2: receipt generation (Shopify-style, pure)

**Branch:** `feature/pay2-receipt-generation`. Pure, no I/O — provider-independent. `core/payments/receipt.py`:
`Receipt`/`LineItem` (subtotal + optional tax → total), `render_receipt_text` (SMS/WhatsApp) and
`render_receipt_html` (self-contained inline-style HTML for the email body — CSP-safe). **All dynamic
strings HTML-escaped** (a store name/note can't inject markup). Tax is a caller-passed field (rules not
invented, §18). PAY3 delivers what this renders.

**Verify:** ruff clean · `mypy core` 172 · guards 0 · **receipt tests 5** (math, INR formatting, text
fields, **HTML-escaping/no-injection**, tax-row omitted when zero). No I/O, no deps. **Next:** PAY3 —
deliver the receipt to WhatsApp + email (uses PAY0 + WABA send) — but see the new **PAY1b** (provider-
agnostic payments incl. free UPI) raised by the founder first.

## 2026-08-10 — PAY1b: provider-agnostic payments + free UPI-intent provider

**Branch:** `feature/pay1b-provider-agnostic-upi`. Founder: don't lock to Razorpay; want UPI (near-free).
Backend only, no migration, no new dep.

- `core/payments/base.py`: a `PaymentProvider` Protocol (`create_payment_request` → `PaymentRequest`;
  `verify_webhook_signature`; `name`/`auto_confirm`) + `get_payment_provider()` factory (config
  `payment_provider`, default razorpay).
- `core/payments/upi.py`: **`UpiIntentProvider`** — builds a free `upi://pay?…` deep-link + QR payload
  against `upi_vpa` (NPCI intent). Zero cost, no network, **`auto_confirm=False`** (no webhook →
  reconcile); simulated (placeholder VPA) until `upi_vpa` set.
- `RazorpayClient` now implements the interface (`name`/`auto_confirm=True` + `create_payment_request`
  wrapping the payment link); existing `create_payment_link` + PAY1 tests unchanged.
- config: `payment_provider` (default "razorpay") + `upi_vpa` / `upi_payee_name`.

**Confirmation model recorded** (DECISIONS): server-side only — signed webhook (authoritative) + API
status-fetch (UX) + reconciliation polling (backstop); never trust the browser. Auto-receipts need an
auto-confirming provider (a PSP); free bare-QR = manual "mark paid".

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Factory: default Razorpay / config → UPI; both satisfy the Protocol | `test_factory_*` | PASS |
| Razorpay create_payment_request shape (auto_confirm, simulated) | `test_razorpay_create_payment_request_shape` | PASS |
| UPI builds a valid free `upi://` link (pa/am/cu), no auto-confirm | `test_upi_intent_builds_free_link_no_confirm` | PASS |
| UPI simulated without a VPA | `test_upi_intent_simulated_without_vpa` | PASS |
| UPI has no webhook (verify → False) | `test_upi_intent_has_no_webhook` | PASS |

**Commands:** ruff clean · `mypy core` 174 · guards 0 · **unit 455** (+6) · scaffold imports clean · no
external call, no new dep, no secrets. Decision in `DECISIONS.md`. **Next:** PAY3 — deliver the receipt
to WhatsApp + email when the (verified) payment confirms.

## 2026-08-10 — PAY2b: branded receipt redesign (cream/gold, email-safe)

**Branch:** `feature/pay2b-receipt-design`. Founder wanted the receipt *designed*, not generic.
`render_receipt_html` rebuilt in the cream/gold "Atelier" brand — email-client-safe (inline styles +
tables, Georgia serif for wordmark/total, no external fonts): gold top-rule, serif wordmark + "PAYMENT
RECEIPT" eyebrow, a green **PAID** pill, tidy line-item table, emphasized serif **Total paid** in gold,
a payment-reference chip, and a warm footer. All dynamic values still HTML-escaped (no injection). Text
receipt (WhatsApp) unchanged. Preview published for founder review (sample/demo data).

**Verify:** ruff clean · `mypy core` 174 · **receipt tests 5** (math, formatting, **escaping**, tax-row
logic still pass against the new markup) · guards 0. **Next:** PAY3 — deliver it on verified payment.

## 2026-08-10 — PAY-TX: Transactions store (meaningful number, %-discount, notes, retrievable)

**Branch:** `feature/pay-tx-transactions`. Founder wanted every charge stored + retrievable with a
meaningful auto-number, discounts, and notes. Also chose the number scheme **`{STORE}-{YYMM}-{seq}`**
(store code · YYMM · per-store monthly sequence) and **percent** discounts.

- **Migration `8508f4155753`** (on `b6123061f10b`): `transactions` table (org_id, receipt_no,
  store_code, period_ym, seq, line_items jsonb, subtotal/discount/tax/total minor, discount_percent +
  reason, notes, provider/provider_ref, status, contacts, timestamps; UNIQUE(org_id,receipt_no) +
  UNIQUE(org_id,period_ym,seq); **RLS**). **Up/down/up verified**.
- **Service** (`core/payments/transactions.py`): `store_code` (name→"RATNA"), immutable auto-number,
  `create_transaction` (percent discount via **Decimal — no float on money**, subtotal/total computed),
  `list`/`get`, `to_receipt` (builds the PAY2 receipt incl. discount line). Sets `set_org_context` (RLS).
- **API** (`core/payments/api.py`, registered): `POST/GET /v1/admin/tenants/{org}/transactions[/{id}]`
  — admin-plane gated (MANAGE create / READ list+get), audited with target_org_id.
- **Receipt** (`receipt.py`): gained a discount line (text + branded HTML); total = subtotal − discount + tax.

**Requirement → evidence:**
| Criterion | Test | Result |
|---|---|---|
| Auto-number `RATNA-YYMM-001`, seq increments, %-discount math, retrieve | `test_create_numbers_discounts_and_retrieves` | PASS |
| One store's transactions never show under another (RLS) | `test_transactions_isolated_between_stores` | PASS |
| 403 non-operator / 404 unknown id | `test_transactions_403…` / `…404…` | PASS |
| store_code + to_receipt (discount label, totals) | `tests/unit/test_transactions.py` (3) | PASS |
| Discount reduces total + shows on receipt | `test_discount_reduces_total_and_shows_on_receipt` | PASS |

**Commands (full CI mirror before push):** ruff clean · guards 0 (fixed a **float-money** violation —
Decimal, not float) · `mypy core` 176 · migration up/down/up · **full tests/unit 459** · isolation +
payments/billing/tenants integ 41. **Next:** PAY3 — approval-gated receipt delivery (draft→approve→send).

---

## 2026-08-10 — PAY3 · Approval-gated receipt delivery (branch `feature/pay3-receipt-delivery`) — merged `022f88b`, CI green

**Founder ask:** after charging a store, the receipt must **route through approvals** (not auto-send),
then go to **WhatsApp + email on file** — Shopify-style. **Approved shape:** the human approval is the
gate; delivery uses the gated low-level clients (no separate execution token).

- **`core/payments/delivery.py`** (NEW): `mark_paid_and_request_receipt` sets the tx `paid` and drafts
  a `receipt.send` approval (tier 1) — the operator identity goes in the payload + audit log, since
  `approvals.requested_by` FKs to `agent_instances` (agent-run approvals), not users. `deliver_receipt`
  renders the PAY2 receipt and sends via **gated** `EmailClient` (if email on file) + `MetaClient` (if a
  WhatsApp channel is connected — else skipped gracefully), then marks the tx `receipted`. **Idempotent:**
  a `receipted` tx short-circuits (`already_sent`), so a redelivered `approval.resolved` never re-sends.
- **`core/payments/receipt_consumer.py`** (NEW): consumer on `approval.resolved.v1`, own group
  "receipt-delivery" (independent of runtime-resume on the same stream). Delivers only on
  `decision == approved` for a `receipt.send` approval; rejected/expired send nothing. Registered in
  **`core/worker.py`**.
- **`core/payments/api.py`**: `POST /v1/admin/tenants/{org}/transactions/{tx_id}/request-receipt` →
  202 `{approval_id, receipt_no, status: pending_approval}`. 404 unknown tx · 409 already receipted ·
  admin-plane gated (TENANTS_MANAGE) · audited `receipt.requested`.

**Requirement → evidence** (`tests/integration/test_receipt_delivery.py`):
| Criterion | Test | Result |
|---|---|---|
| Request marks tx paid + drafts a pending `receipt.send` approval (nothing sent) | `test_request_receipt_marks_paid_and_queues_approval` | PASS |
| 409 when already receipted · 404 unknown · 403 non-operator | `…409_when_already_receipted` / `…404_for_unknown_tx` / `…403_for_non_operator` | PASS |
| Delivery sends email + is idempotent (2nd call = already_sent, stays receipted) | `test_deliver_receipt_sends_email_and_is_idempotent` | PASS |
| WhatsApp skipped gracefully when no channel connected | `test_deliver_receipt_skips_whatsapp_when_no_channel` | PASS |
| WhatsApp sent when a channel is connected (simulated) | `test_deliver_receipt_sends_whatsapp_when_channel_connected` | PASS |
| Missing tx is a no-op | `test_deliver_receipt_missing_tx_is_no_op` | PASS |
| Consumer delivers only on approved; ignores rejected | `test_consumer_delivers_only_on_approved` / `test_consumer_ignores_rejected` | PASS |

**Commands (full CI mirror before push):** ruff `All checks passed!` · guards 0 · `mypy core` 178 ·
**full tests/unit 459** · new integ `test_receipt_delivery.py` **10** · payments+approvals/events integ
**77** (no regressions). **Security:** no real send without gate + live provider (§10.4); no secrets/OTP
in logs or events; RLS enforced via `set_org_context`/`org_scoped_session`; operator-only surface.
**Next:** PAY3b (Razorpay webhook endpoint) + operator "Charge this store" UI; then OC5–OC12.

---

## 2026-08-10 — Operator "Charge this store" UI (branch `feature/pay-ops-ui-charge-store`) — merged `ec0828f`, CI green

**Founder ask:** after onboarding, be able to **charge the store** and generate/send a Shopify-style
receipt (routed through approvals). This is the operator-facing front for PAY-TX + PAY3.

- **`web-ops/src/api.ts`**: `Transaction`/`TxLineItem`/`NewTransactionInput`/`ReceiptRequestResult`
  types + `adminListTransactions` / `adminCreateTransaction` / `adminRequestReceipt`.
- **`web-ops/src/lib/receipts.ts`** (NEW, pure + tested): `toMinor` (₹→paise), `subtotalMinor`,
  `previewTotals` (subtotal − discount% + tax, half-up rounding matching the server Decimal),
  `hasChargeableLine`, `statusView` (status → label+tone), `canRequestReceipt`.
- **`web-ops/src/components/StorePaymentsSection.tsx`** (NEW): a **Payments · charge this store** card
  on the store-360 page — collapsible **New charge** form (multi-line items, %-discount + reason, tax
  label/amount, notes, receipt email/WhatsApp, live total preview) → `adminCreateTransaction`; a
  **transactions table** with a **Request receipt** action → `adminRequestReceipt` (drafts the PAY3
  approval; inline "queued — awaiting the owner's approval" note). Status chip: Awaiting receipt →
  Receipt pending approval → Receipt sent. Charge/request gated on `platform.tenants:manage`.
- **`web-ops/src/components/StoreReportsSection.tsx`**: mounts the payments card (read-gated).

**Requirement → evidence:** `web-ops/src/lib/receipts.test.ts` (8) — ₹→paise, subtotal, discount+tax
preview + half-up rounding, chargeable-line validation, status label/tone, request-receipt gating.

**Commands (web gate):** `npm run lint` (oxlint) clean · `npx tsc -b --noEmit` 0 · `vitest run` **28
passed** (6 files) · `npm run build` ✓ · repo `scripts/guards.py` 0 (industry-nouns scans web-ops too).
**Security:** operator-only surface; writes gated on `platform.tenants:manage`; no secrets; receipt
still can't send without the PAY3 approval + a live provider. **Next:** PAY3b — Razorpay webhook
endpoint (payment confirmation), then OC5–OC12 forecast backlog.

---

## 2026-08-10 — Note · Receipt delivery format (PAY3 clarification, founder Q)

How a receipt is delivered today (see `core/payments/delivery.py`):
- **Email** → the **branded HTML** receipt (`render_receipt_html`: cream/champagne, serif wordmark,
  PAID pill, discount row) with a plain-text alternative — the "designed nicely" version.
- **WhatsApp** → a **formatted plain-text message** (`render_receipt_text` via `MetaClient.send_text`).
  It is **not a PDF and not an image**.

A PDF/document on WhatsApp is a distinct future enhancement (**PAY4 · receipt PDF**): render the receipt
to a PDF (needs a PDF library — a **new dependency**, so founder approval per §9) or an image → upload it
to Meta's media endpoint for a `media_id` → send a `document` (or `image`) message. Not built; awaiting
founder decision on whether it's wanted and which renderer. **Order stands:** PAY3b next, then PAY4 /
OC5–OC12 by founder pick.
