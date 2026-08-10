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
- **Code readiness (2026-08-10, MVP-076):** the send/connect/template code path is **real-ready** — `MetaClient` (`core/channels/whatsapp/meta_client.py`) has the real Meta Graph-API httpx calls gated behind `whatsapp_live_enabled` (simulated when off), wrapped by the 5-gate `send()` + bounded 429/5xx retries, and the **live path is now test-covered** (`tests/unit/test_meta_client_live.py`, httpx mocked — real request shape + parse verified with no network). Only the external items below remain; **no code change** is expected to go live.
- **Next action:** Founder decides the number question, then submits Meta verification. Go-live = flip `GROWTH_OPERATOR_WHATSAPP_LIVE_ENABLED=true` + connect the real number (`POST /v1/channels/whatsapp/connect`, stores real creds per org); sends still require approval + execution token.

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

### ~~12. WhatsApp media — real clamav/MinIO adapters~~ — RESOLVED 2026-07-31 (live-verified)

- **Severity:** Medium — dev/test only; must not reach production as-is.
- **Owner:** Founder (dependency + infra approval) → Engineering (wire adapters).
- **Description:** MVP-037 media handling ships with **simulated** adapters (`SimulatedScanner` returns clean, `SimulatedStore` keeps bytes in-process) so the download→scan→store flow is testable with no new dependencies (§9, founder-approved 2026-07-31). The real clamav AV scanner and MinIO/S3 object store are **not** added (no deps, no docker-compose services). A no-op AV scanner reaching production would pass malware, so `media_av_enabled` / `media_storage_enabled` **fail closed** (`NotImplementedError`) until the real adapters exist — the simulated scanner can only run with the flags off (dev default).
- **Resolution (2026-07-31, code):** Founder approved (§9). Added `clamd` + `boto3`; `media.py` `ClamavScanner` (clamd instream, fail-closed) + `S3Store` (boto3 → MinIO/S3) behind `media_av_enabled`/`media_storage_enabled` (flags off → simulated; a bad service address raises `MediaScanError` → quarantine, never clean). `clamav` + `minio` added to `docker-compose.dev.yml` under an opt-in `media` profile; config gained clamav/S3 settings. Tests `tests/integration/test_media_adapters.py` (clean-vs-EICAR scan, S3 round-trip, real-ingest, fail-closed) **skip fast** when the ports are down. **296 passed, 4 skipped.**
- **Verified live (2026-07-31):** both services brought up (`docker compose --profile media up`); all 5 `test_media_adapters` tests pass — ClamAV flags the EICAR test signature + passes clean bytes, MinIO stores + returns the bytes, full ingest stores, scanner-error quarantines. Full suite **300 passed, 0 skipped**. ClamAV publishes no arm64 image, so the compose service is pinned `platform: linux/amd64` (emulated on Apple-Silicon dev, native on amd64 servers). Meta media download/upload stays gated (#3) until API access lands.

### ~~13. Signed-bundle .tar.zst transport deferred (needs zstandard dep)~~ — RESOLVED 2026-07-31

- **Severity:** Low — dev installs from a directory; the security-critical verification is done.
- **Owner:** Founder (dependency approval) → Engineering.
- **Description:** MVP-039 implements the pack digest manifest (`MANIFEST.sha256`) + ed25519 signature verification over a **directory** (tampered file → refused; invalid signature → refused), which is the trust-critical part. The `.tar.zst` packaging/unpacking of a signed bundle needs the `zstandard` dependency (not present; not added per §9). Deferred — it's only compression/transport around the already-verified tree.
- **Resolution (2026-07-31):** Founder approved adding `zstandard` (0.25). `core/packs/bundle.py` gained `pack_bundle` (tree + MANIFEST + ed25519 sig → `.tar.zst`) and `unpack_bundle` (size-capped via `frame_content_size`, `data`-filter extraction — no path traversal); `load_bundle` now transparently unpacks a `.tar.zst`, then verifies + parses. 4 new tests (round-trip, dev/prod load, wrong-key refused, size-cap). The publisher signing tool + real platform public key remain separate (out of MVP-039 scope).

### 14. Pack installer: attribute freeze + credential revocation deferred (policies + workflows now seeded)

*(Update 2026-07-31: MVP-045 created `catalog_items` — the installer's uninstall attribute-freeze and **MVP-042 index generation** are now unblockable; policies/workflows still wait on 014/016.)*
*(Update 2026-08-09: **workflows-half RESOLVED** — MVP-072 migration 036 created `workflow_definitions`; the installer's `_seed_workflows` step now parses + seeds each pack workflow as an active definition. `DEFERRED_STEPS` is now empty. Policies were resolved earlier by MVP-044. What remains open is only uninstall's **attribute freeze** + **credential revocation**.)*

- **Severity:** Low — the install pipeline is complete for all steps; only two **uninstall** cleanup steps remain deferred.
- **Owner:** Engineering (unblocks when the uninstall-cleanup ticket lands).
- **Description:** MVP-040's install pipeline now runs **all** steps against existing tables (catalog schema, prompt layers, bindings, paused instances, **policies** → `approval_policies` [MVP-044], **workflows** → `workflow_definitions` [MVP-072, migration 036]). Still deferred: uninstall's **attribute freeze** (`catalog_items`) and **credential revocation**. Uninstall currently pauses instances + marks the install `uninstalled` + retains the catalog schema + leaves L3 untouched.
- **Partially resolved (2026-08-05, MVP-044):** the **policies** step is now implemented — `_seed_policies` seeds `approval_policies` (scope='pack') from the bindings `tier_defaults` (+ migration `b6456b200baa` gave app_rw a tight pack-only INSERT RLS). Prompt-layer seeding was already implemented. **Still deferred:** the **workflows** step (`workflow_definitions`, 016/MVP-072 table not built) and uninstall's **attribute freeze** / **credential revocation**.
- **Next action:** MVP-072 builds the 016 workflows table; then implement `_seed_workflows`. Attribute freeze wires in with a later uninstall pass.

### ~~20. Seeded pack policies don't fire — tool→action bridge missing (MVP-044)~~ — RESOLVED 2026-08-05

- **Severity:** Medium — drafts stay **safe** (an un-matched tier-eval action fails safe to tier-2 → approval); the gap is that the pack's *specific* tiers (e.g. reply=tier1 auto-send, high-value-quote=tier2) don't apply, so everything over-approves.
- **Owner:** Engineering (proxy/engine) + Founder (confirm the action taxonomy).
- **Description:** MVP-044 seeds `approval_policies` keyed on the pack's **abstract** actions (`action.message.send`, `action.quote.send`, `action.campaign.execute`, `action.catalog.write`). But the mediation proxy queries the policy engine by **tool name** (`messages.send`, `campaigns.execute`, …) — see `proxy._engine_tier`. So the seeded pack rules are faithful data-of-record but never match a tool call. `action.quote.send` has no 1:1 tool (a quote is a `messages.send` whose content is a price), so it's a taxonomy question, not a rename.
- **Resolution (2026-08-05):** the engine now maps tool→abstract-action **family** (`engine.evaluate_tool` / `resolve_actions`) and the proxy calls it; a `messages.send` is *also* `action.quote.send` when it carries a price (structured `amount_minor` or a figure parsed from the body via MVP-054's `extract_amounts`). Contributors are **pooled across the family** and the empty-set fallback applied once, so a small no-discount quote falls back to the message tier instead of fail-safing to tier-2. Optional-attribute conditions in the pack (`discount_minor`, `sentiment`, `topic`) were `has()`-guarded so an absent field means "not met" rather than fail-safe-matching. Verified end-to-end through the real proxy: plain reply → tier-1 (auto), ₹1,50,000 quote → tier-2 (parks for approval).

### ~~15. docs/ symlink replaced by a stray directory~~ — RESOLVED 2026-07-31

- **Severity:** Medium (transient) — broke doc access + 3 event-type tests (topics.yaml unreachable).
- **Description:** The tracked `docs` symlink (→ ../Growth-Operator-Vault) was replaced in the working tree by an unrelated stray directory; `git status` showed `D docs`. The vault itself was intact.
- **Resolution (2026-07-31, founder-approved):** moved the stray dir out of the repo to `../growth-operator-docs-stray-backup` (nothing deleted), then `git checkout -- docs` restored the symlink. Verified `docs -> ../Growth-Operator-Vault`, topics.yaml reachable, full suite 315 passed. Not committed (docs was never staged).

### 16. Embedding provider not selected + scheduler not wired (MVP-048)

- **Severity:** Low — semantic search runs gated-simulated; real similarity awaits a provider.
- **Owner:** Founder (provider choice + §9 dep + credentials) → Engineering (wire adapter + scheduler).
- **Description:** MVP-048 ships a **deterministic simulated embedder** (no paid API) so the hybrid pipeline (kNN HNSW + RRF + empty→nearest) is fully built and tested. The real hosted embedding provider is **not chosen/wired** — enabling `embeddings_provider_enabled` fails closed (`NotImplementedError`).
- **Partially resolved (2026-08-04, #16 worker/scheduler wiring):** the process entrypoints are now real — `core/scheduler.py` installs + fires `embeddings_batch` (every 5 min, `SimulatedEmbedder`) alongside the approval ladder / trust settle / dedupe prune under the per-(job, minute) lock, and `core/worker.py` runs the outbox publisher + registered consumers. Verified with a live-broker boot smoke (`approval_ladder` fired, both processes shut down gracefully). **Still open (this blocker):** the *real* embedding provider — a founder decision.
- **Next action (remaining):** Founder picks an embedding provider (OpenAI / Voyage / Cohere / self-hosted), approves the client dep (§9) + credentials; then implement the real `Embedder` behind the `embeddings_provider_enabled` flag. The batch is already wired to fire — only the simulated→real embedder swap remains. The 1024-dim column + HNSW index are already in place (012).

### 17. `catalog.price_inputs_changed` typed event deferred (MVP-049)

- **Severity:** Low — the MVP-visible signal (the `stale_inputs` flag) is written **synchronously** and tested; only the async fan-out to other consumers is deferred.
- **Owner:** Founder (approve the vault event-catalog addition) → Engineering (register + emit).
- **Description:** MVP-049 flags open quotes `stale_inputs` directly from the catalog update path (`availability.flag_quotes_if_price_inputs_changed`). The spec (`docs/21-platform/catalog-abstraction.md`) also names a typed `catalog.price_inputs_changed` event, but its payload schema must live in the vault's **read-only** `docs/implementation/events/topics.yaml` (§4), and it is not yet registered — `emit()` rejects unregistered types and the event-catalog drift test enforces this. So the event is **not** emitted yet.
- **Next action:** Founder approves adding `catalog.price_inputs_changed` (payload: `item_id: uuid`, `changed_keys: array`) to `topics.yaml` in the vault; then regenerate `core/events/types.py` (`gen_events.py`) and emit it alongside the flag write (transactional outbox). No schema/flag change needed — the flag already works.

### 21. Support-tickets track lands outside the vault (schema, migration order, module map, topics.yaml)

- **Severity:** Low — documentation/reconciliation only; the DB is correct, migrated, RLS-enforced, isolation-tested (599 pytest). No runtime impact.
- **Owner:** Founder (vault reconciliation) → Engineering (register the event once the vault adds it).
- **Description:** the support-tickets slice + hardening + Phase-1 RBAC add tables/columns outside the vault: `support_tickets` + `platform_admins` (**018**), `platform_access_log` (**019**), `platform_admins.expires_at` (**020**), the `user_orgs`/`invites` role-CHECK widen + RBAC-catalog reseed (**021**), `platform_admins.role` (**022**); plus new modules `core/support/`, `core/tenancy/platform_admin.py`, `core/tenancy/platform_permissions.py` — none in the vault `docs/06-database/schema.sql`, the migration-order doc, or the core module map (same posture as `incidents`/`import_batches`). Also the vault `roles/permissions` catalog description predates the Phase-1 role model (owner/manager/staff/viewer + retired `founder`). Separately, the `support.ticket.raised.v1` outbox event is **not** emitted: like #17, its payload schema must first be registered in the vault's read-only `topics.yaml` (§4, enforced by the drift test); the operator queue reads by poll, so the loop does not need it.
- **Next action:** on the next vault pass, add the tables to `schema.sql`, note migrations 018–022 in the order doc, add `core/support` + the two `platform_*` modules to the module map, update the RBAC role model in the auth-rbac spec, and (for operator notifications) register `support.ticket.raised.v1` (payload e.g. `ticket_id: uuid`, `priority`, `severity`) in `topics.yaml` → regenerate `types.py` → emit on raise.

### ~~19. Vault `schema.sql` stale for the approvals cluster (reconciliation)~~ — RESOLVED 2026-08-04

- **Severity:** Low — documentation only; the database is correct, migrated, RLS-enforced, and tested (518 pytest). No runtime impact.
- **Description:** the authoritative vault `docs/06-database/schema.sql` predated MVP-065/067/068/070. It defined only a v1 `approvals` (with `tenant_id`, `requested_by NOT NULL REFERENCES agents(id)`, `approver`, no tier bound) and **none** of the four policy-engine tables (`approval_policies`, `trust_ledger`, `incident_tightening`, `execution_token_jti`). The shipped schema uses `org_id`, `requested_by`→`agent_instances` (nullable), `approver_user_id`, +11 feature columns.
- **Resolution (2026-08-04):** reconciled doc→code (founder-approved, DECISIONS): the DB is canonical. The founder applied the drafted patch (replace the `approvals` block + add the four tables) to the vault `schema.sql`; verified — all 5 tables now match the live DB **column-for-column** (approvals 23/23, approval_policies 14/14, trust_ledger 6/6, incident_tightening 7/7, execution_token_jti 7/7). Canonical reference + provenance kept in [approvals-schema.md](approvals-schema.md). No code/migration change. The vault is not git-tracked (Obsidian), so the file save is the record.
- **Still open (separate, out of scope):** the vault's `agents` table is unimplemented (runtime uses `agent_instances`) — a broader agent-model reconciliation for a future pass.

### ~~18. CI red on every push — migrate-job role order + vault-dependent unit tests~~ — RESOLVED 2026-08-03

- **Severity:** Medium — `main` CI (`test` + `migrate` jobs) failed on every push, emailing the founder; local runs were green, so it went unnoticed.
- **Description:** Two independent bugs. (1) The `migrate` job ran `alembic upgrade head` **before** creating the `app_rw` role, but migration 006 does `REVOKE … FROM app_rw` → `role "app_rw" does not exist`. (2) Three unit drift tests (`test_archetypes`, `test_event_types`, `test_events_topics`) read source-of-truth files under `docs/implementation/…`, but `docs/` is a symlink to the **private vault that isn't checked into GitHub** → `FileNotFoundError` in CI.
- **Resolution:** (1) Reordered the `migrate` job to run `infra/db/roles.sql` (idempotent, sets DEFAULT PRIVILEGES, guards its own `audit_log` REVOKE with `IF EXISTS`) **before** `alembic upgrade head` — mirrors `make bootstrap`. (2) The three tests now `pytest.skip` when the vault file is absent (same pattern as DB-integration tests skipping when the DB is unreachable) — the drift check runs locally where the vault is present. Fix branch `fix/ci-app-rw-and-vault-tests`.
- **Follow-up (done 2026-08-03):** the two spec files were vendored into `spec/` (`spec/events/topics.yaml`, `spec/agents/tool-permissions.yaml`) so codegen + the drift tests run in CI without the vault; the CI actions were also bumped to node24 (checkout@v5, setup-uv@v7).

### ~~Test date off-by-one in test_business_metrics~~ — RESOLVED 2026-08-09

- **Severity:** Low (test-only). **Description:** `test_business_metrics.py` seeded leads with `datetime.now(UTC)` but asserted against `date.today()` (local), a UTC/local off-by-one that went red only when the local/UTC date boundary differed — surfaced when the system date rolled over mid-session (it passed during MVP-073a/b earlier the same day). Confirmed pre-existing (fails on main with the stage-3 work stashed), not a workflow-engine regression.
- **Resolution:** aligned the test's day basis to `datetime.now(UTC).date()` (MVP-073c change set). Full suite green.
