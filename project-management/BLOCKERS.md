# Blockers

Unresolved problems. Update in place as status changes; move to a strikethrough "Resolved" section (with date + commit) rather than deleting, so the history stays visible.

---

### ~~1. Docker Compose env var prefix mismatch~~ — RESOLVED 2026-07-22

- **Severity:** Medium (latent — not yet triggered by any running code)
- **Owner:** Engineering (founder)
- **Description:** `infra/docker/docker-compose.dev.yml` sets `DATABASE_URL` / `REDIS_URL` on the `api`/`worker`/`scheduler` services. `core/common/config.py`'s `Settings` reads `GROWTH_OPERATOR_DATABASE_URL` / `GROWTH_OPERATOR_REDIS_URL` (env_prefix). Containers will silently fall back to `Settings` defaults (`@localhost`) instead of the `postgres`/`redis` service hostnames.
- **Resolution (2026-07-22):** Renamed both vars to `GROWTH_OPERATOR_DATABASE_URL` / `GROWTH_OPERATOR_REDIS_URL` on all three services in `docker-compose.dev.yml` (branch `feature/mvp-011-otp-auth`, not yet committed). Static verification only — the containers still have not been booted against a live Docker daemon (see #2), so the end-to-end connection has not been exercised.

### ~~2. `make dev` / `alembic upgrade head` never verified against a live Docker daemon~~ — RESOLVED 2026-07-22

- **Severity:** Medium
- **Owner:** Founder
- **Description:** Earlier Claude Code sessions could not reach a Docker daemon, so all verification was static and migration 001 / the auth routes were only checked via offline SQL + TestClient up to the pre-DB path.
- **Resolution (2026-07-22):** Founder installed OrbStack + Docker Desktop. Daemon reachable (server 29.6.2, compose v5.3.1). Brought up `postgres` + `redis` (both healthy, ports 5432/6379). Verified: `alembic upgrade head` applies migration 001; `alembic downgrade base` drops all tables and `upgrade head` recreates them (down-migration proven); the three identity tables have the expected columns/indexes/CHECKs. Added `tests/integration/test_auth_flow.py` (skips without a DB) exercising the full request→verify→token flow against the live DB — **48 tests pass** (45 unit + 3 integration). MVP-011's live-DB acceptance is now met.
- **Still outstanding (tracked elsewhere, not this blocker):** the full `make dev` boot of the *app* containers (api/worker/scheduler images) hasn't been run — only the data services. And "OTP to founder's real inbox in staging" still needs a real email provider (TODO #2) + a deployed staging env.

### 3. Meta WABA (WhatsApp Business API) verification not started / status unknown

- **Severity:** High — longest lead-time item in the entire MVP plan; blocks MVP-031..037 and the Week-1 exit demo
- **Owner:** Founder
- **Description:** Per `docs/25-implementation-starter-kit/02-week-1-plan.md`, WABA verification submission should start Day 1, in parallel with code. A decision is required first: Srila's existing number vs. a new number for Priya (porting freezes the number for days).
- **Next action:** Founder decides the number question, then submits Meta verification immediately.

### 4. Missing dependencies for planned modules

- **Severity:** Low (not yet needed)
- **Owner:** Engineering
- **Description:** `langgraph` (named in `docs/25-implementation-starter-kit/06-backend-plan.md` as the `core/runtime` stack choice) and `jsonschema` (needed for Draft-2020-12 catalog/pack attribute validation, `core/catalog`, pack verification) are not in `pyproject.toml`.
- **Next action:** Add `langgraph` before MVP-055; add `jsonschema` before MVP-042/046.

### 5. IBJA gold-rate source undecided

- **Severity:** Medium — needed by MVP-051 (Week 2 Day 4 per plan)
- **Owner:** Founder
- **Description:** Open question: scrape vs. paid API vs. manual-first. `fetch_spec` currently assumes an API exists; manual-entry is the documented hedge.
- **Next action:** Founder decides; manual-first can unblock MVP-051 without waiting on an API integration decision.

### 6. Razorpay account entity undecided

- **Severity:** Medium — needed before payment links (MVP-053/054 area)
- **Owner:** Founder + counsel
- **Description:** Personal account vs. new company entity; ties to an open founder-IP legal question.
- **Next action:** Founder + counsel decide before payment-link work starts.

### 7. React 18 (spec) vs. React 19 (as scaffolded) unresolved

- **Severity:** Low
- **Owner:** Founder / Engineering
- **Description:** `docs/25-implementation-starter-kit/06-backend-plan.md` specifies React 18; `npm create vite@latest` installed React 19.2.7 (latest at scaffold time) into `web/package.json`.
- **Next action:** Decide to pin `web/` to React 18 or update the authoritative doc to React 19.

### 8. Data residency (Hetzner EU vs. India VPS) undecided

- **Severity:** Low for pilot scale (documented exception acceptable); becomes higher severity before scaling
- **Owner:** Founder
- **Description:** DPDP posture question; not blocking for pilot tenants.
- **Next action:** Decide before MVP-098 (production infra) / before scaling past pilot tenants.

### ~~9. Git commit identity auto-detected, not explicitly configured~~ — RESOLVED 2026-07-30

- **Severity:** Low
- **Owner:** Founder
- **Description:** Commits through `b57648b` used an auto-detected identity (`Sri Eswaroju <srila@mac.attlocal.net>`) because no git user.name/user.email was set.
- **Resolution (2026-07-30):** Set globally — `Sri Eswaroju <saisrikanth.eswaroju@gmail.com>` (the founder's GitHub email, so commits attribute to their GitHub account). Applies to future commits only; already-pushed history left as-is (no rewrite).

### 10. MVP-009 staging cannot be applied (scaffold only)

- **Severity:** High — P0 ticket; staging is needed before Week-1 D5 so WhatsApp tickets test against it.
- **Owner:** Founder
- **Description:** MVP-009 Terraform (`infra/terraform/staging/`) + `deploy-staging.yml` are written but **un-applied**. Blocked on: a Hetzner Cloud account + API token; a domain + DNS provider (`api.staging.<domain>`); the data-residency decision (see #8 — Hetzner EU vs India VPS); and Meta WhatsApp test-number access (pending, tied to #3). terraform is also not installed locally, so the scaffold is not `fmt`/`plan`-validated.
- **Next action:** Founder creates the Hetzner token, picks a domain + residency, sets repo secrets (`STAGING_HOST`, `STAGING_SSH_KEY`, vars `STAGING_ENABLED`/`STAGING_DOMAIN`); then `terraform plan` review before any apply (§8/§10.5 — provisioning needs explicit approval).

### ~~11. RLS is defined but NOT enforced for the app (app connects as a superuser)~~ — RESOLVED 2026-07-29 (MVP-016)

- **Resolution (2026-07-29, MVP-016):** Added the non-superuser, **NOBYPASSRLS** `app_rw` role (`infra/db/roles.sql`, `make db-roles`) and split the DB URLs — the app/worker/scheduler run as `app_rw` (`database_url`) so RLS is enforced, while alembic keeps DDL rights via `database_migrator_url` (owner). `get_db`/`org_scoped_session` set `app.org_id`/`app.user_id` transaction-locally. **Proven live:** `tests/isolation/test_tenant_context.py` (request sees only its JWT org; no token → zero rows; A can't see B; worker wrapper isolates) + a full uvicorn-as-app_rw smoke. app_rw verified `super=false bypassrls=false`. **Still forward:** staging/prod must run `roles.sql` with a SOPS-sourced password before app start; the compose app *image* hasn't been booted via `make dev` (smoke used host uvicorn).
- **Severity:** High (security) — tenant isolation is not actually active yet.
- **Owner:** Engineering (resolve in MVP-016).
- **Description:** The app's DB URL uses `growth_operator`, which is a **superuser with `bypassrls=true`** (verified 2026-07-29). Postgres RLS (including `FORCE ROW LEVEL SECURITY`) is bypassed for superusers/BYPASSRLS roles, so the migration-002 `user_orgs` policies — and every future org-scoped policy — do nothing for the running app. There is no `app_rw` role yet. The policies ARE proven correct under a constrained role (`tests/integration/test_orgs_flow.py::test_user_orgs_rls_isolates_under_constrained_role`).
- **Next action (MVP-016):** create a non-superuser, non-BYPASSRLS `app_rw` role with the right GRANTs (+ default privileges for future tables), keep migrations running as an owner/`migrator` role, and point `GROWTH_OPERATOR_DATABASE_URL` (app + worker + scheduler) at `app_rw`. Then MVP-016's "unset context → zero rows" acceptance becomes real. Until then, do not treat multi-tenant isolation as enforced.

### 12. WhatsApp media uses simulated AV + storage (real clamav/MinIO not wired)

- **Severity:** Medium — dev/test only; must not reach production as-is.
- **Owner:** Founder (dependency + infra approval) → Engineering (wire adapters).
- **Description:** MVP-037 media handling ships with **simulated** adapters (`SimulatedScanner` returns clean, `SimulatedStore` keeps bytes in-process) so the download→scan→store flow is testable with no new dependencies (§9, founder-approved 2026-07-31). The real clamav AV scanner and MinIO/S3 object store are **not** added (no deps, no docker-compose services). A no-op AV scanner reaching production would pass malware, so `media_av_enabled` / `media_storage_enabled` **fail closed** (`NotImplementedError`) until the real adapters exist — the simulated scanner can only run with the flags off (dev default).
- **Next action:** Founder approves adding the object-storage client (minio/boto3) + a clamd client as dependencies and MinIO + clamav services to `docker-compose.dev.yml`; then wire the real `MediaScanner`/`MediaStore` adapters behind the two flags and enable them in staging/prod. Meta media download/upload also stays gated (#3) until API access lands.

### 13. Signed-bundle .tar.zst transport deferred (needs zstandard dep)

- **Severity:** Low — dev installs from a directory; the security-critical verification is done.
- **Owner:** Founder (dependency approval) → Engineering.
- **Description:** MVP-039 implements the pack digest manifest (`MANIFEST.sha256`) + ed25519 signature verification over a **directory** (tampered file → refused; invalid signature → refused), which is the trust-critical part. The `.tar.zst` packaging/unpacking of a signed bundle needs the `zstandard` dependency (not present; not added per §9). Deferred — it's only compression/transport around the already-verified tree.
- **Next action:** Founder approves adding `zstandard`; then add `pack_bundle` pack/unpack around `load_bundle` (compute/verify manifest on the unpacked tree). The publisher-side signing tool + real platform public key are separate (publisher keys are explicitly out of scope for MVP-039).
