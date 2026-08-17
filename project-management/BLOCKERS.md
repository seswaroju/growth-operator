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
- **Direction (2026-08-12):** leaning **existing store number + apply for WhatsApp API access** (recognized by customers); still debating vs. a fresh Indian number. No code change either way — kept open pending the founder's final call + Meta submission.

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
- **Direction (2026-08-12):** use the community IBJA API (`0xSaurabhx/IBJA-API`, hosted at `https://ibja-api.vercel.app/latest`, GET, no key) **for now** to unblock. Fallback order the founder set: **(1) the repo API → (2) manual daily entry → (3) IBJA's official API [contacted, no response yet] → (4) another paid third-party.** Kept OPEN (community endpoint is best-effort).
- **Partially resolved (2026-08-12) — code wired:** `HttpRateFetcher.fetch` now calls the IBJA endpoint (config `rates_ibja_url`, `fetch_spec.url` override) and parses `/latest` into paise/gram (`parse_ibja_gold`: 999→24K, 916→22K, 750→18K, 585→14K; PM session preferred, AM fallback; ×100 ₹/g→paise). Gated on `rates_provider_enabled` (fail-closed off = `SimulatedRateFetcher`); only `ibja_gold` is wired (silver/others stay manual); HTTP/parse errors → `provider_unavailable` (fail-safe). Tests: `tests/unit/test_ibja_rate.py` (9, httpx mocked — no network). **Still OPEN:** go-live = founder flips `GROWTH_OPERATOR_RATES_PROVIDER_ENABLED=true` + verifies a known day's rate against IBJA (the per-gram vs per-10g unit assumption); manual entry remains the fallback; official/paid API is a later swap.

### 6. Razorpay account entity undecided

- **Severity:** Medium — needed before payment links (MVP-053/054 area)
- **Owner:** Founder + counsel
- **Description:** Personal account vs. new company entity; ties to an open founder-IP legal question.
- **Next action:** Founder + counsel decide before payment-link work starts.
- **Direction (2026-08-12):** founder will **add "software services" to the existing (beverage) Pvt Ltd company (MEA)** and open the **Razorpay account under that entity** — a company entity, not personal. Founder-side action (incorporation amendment + Razorpay onboarding); no code change (the payment adapter already records manual/UPI meanwhile). Kept open until the Razorpay account + keys exist.

### ~~7. React 18 (spec) vs. React 19 (as scaffolded) unresolved~~ — RESOLVED 2026-08-12 (bless React 19)

- **Severity:** Low
- **Owner:** Founder / Engineering
- **Description:** `docs/25-implementation-starter-kit/06-backend-plan.md` specifies React 18; `npm create vite@latest` installed React 19.2.7 (latest at scaffold time) into `web/package.json`.
- **Resolution (2026-08-12):** Founder blessed **React 19** ("*feels latest*"). No code change — `web/` + `web-ops/` already run React 19 (tsc + build + vitest green throughout). Recorded in DECISIONS. **Vault reconciliation (founder-side, read-only):** update `docs/25-implementation-starter-kit/06-backend-plan.md` to say React 19.

### 8. Data residency (Hetzner EU vs. India VPS) undecided

- **Severity:** Low for pilot scale (documented exception acceptable); becomes higher severity before scaling
- **Owner:** Founder
- **Description:** DPDP posture question; not blocking for pilot tenants.
- **Next action:** Decide before MVP-098 (production infra) / before scaling past pilot tenants.
- **Direction (2026-08-12):** founder leans **India-resident hosting** if it's cleaner for DPDP + not much pricier (target very low cost). Provider pricing guidance given to the founder (India regions of DigitalOcean/AWS/OVH + budget Indian hosts; a realistic small VPS running Postgres+Redis+app is ~₹400–700/mo, not ₹150 — ₹150 only buys shared hosting that can't run this stack). Founder to pick a provider; #10 (staging) follows.

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
- **Direction (2026-08-12):** blocked on #8 (residency) — founder leans India-resident, which means **Hetzner (EU) is likely NOT the host** (DPDP prefers India-resident; Hetzner has no India region). Founder still needs to **pick a company name + register a domain**. Once the provider (India VPS) + domain exist, engineering rewrites the Terraform target for that provider + does a `terraform plan` for review. Kept open.

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
- **Direction (2026-08-12):** founder chose **OpenAI `text-embedding-3-small`** (cheap, 1536-dim → project uses 1024 via `dimensions` param). Requirement: **meter the cost per store in the ledger** (which store used how much) — same posture as CP-6 LLM costs. Recorded in DECISIONS.
- **Resolved (2026-08-12) — code wired (gated):** `core/catalog/embed.py` now has `OpenAiEmbedder` (async; POST `/v1/embeddings`, `model=text-embedding-3-small`, `dimensions=1024`; key from `embeddings_api_key`; HTTP/parse failure or wrong dims → `EmbeddingError`, fail-closed). The `Embedder` protocol went **async** (Simulated + the 2 call sites — batch + `hybrid_search` — updated; no test churn). `default_embedder` returns `OpenAiEmbedder` when `embeddings_provider_enabled`, else `SimulatedEmbedder`. **Per-store cost metering:** `embed_pending` estimates the batch's tokens and writes a `costs_lite` row (`node_key='embeddings'`, `provider='openai'`) under the org's context — so it surfaces in the **CP-6 cost/margin view** (the LLM-cost line). Config: `embeddings_api_key` / `embeddings_model` / `embeddings_api_base` / `embeddings_price_per_1m_usd`. Tests: `tests/unit/test_openai_embedder.py` (4, httpx mocked) + `test_catalog_embed.py` (cost metered when on, none when off). **Go-live:** founder sets `GROWTH_OPERATOR_EMBEDDINGS_PROVIDER_ENABLED=true` + `..._API_KEY`; the token estimate is a cost approximation (consistent with the LLM `costs_lite` estimates).

### ~~17. `catalog.price_inputs_changed` typed event deferred (MVP-049)~~ — RESOLVED 2026-08-12

- **Severity:** Low — the MVP-visible signal (the `stale_inputs` flag) was already synchronous+tested; this adds the typed fan-out event.
- **Resolution (2026-08-12):** Founder registered `catalog.price_inputs_changed.v1` (payload `item_id: uuid`, `changed_keys: array`) in the vault `topics.yaml`; synced to the vendored `spec/events/topics.yaml`, regenerated `core/events/types.py` (`gen_events.py`), added it to `ALLOWED_EVENT_TYPES`. `availability.flag_quotes_if_price_inputs_changed` now **emits it in the same transaction** as the flag write (only when a *real* price input changed — the changed_keys ∩ strategy deps). Test: `test_availability_stale.py::test_weight_edit_...` asserts the event fires on a weight edit (with `net_weight_g` in `changed_keys`) and **not** on an unrelated (gender) edit. Drift tests green.

### 21. Support-tickets track lands outside the vault (schema, migration order, module map, topics.yaml)

- **Severity:** Low — documentation/reconciliation only; the DB is correct, migrated, RLS-enforced, isolation-tested (599 pytest). No runtime impact.
- **Owner:** Founder (vault reconciliation) → Engineering (register the event once the vault adds it).
- **Description:** the support-tickets slice + hardening + Phase-1 RBAC add tables/columns outside the vault: `support_tickets` + `platform_admins` (**018**), `platform_access_log` (**019**), `platform_admins.expires_at` (**020**), the `user_orgs`/`invites` role-CHECK widen + RBAC-catalog reseed (**021**), `platform_admins.role` (**022**); plus new modules `core/support/`, `core/tenancy/platform_admin.py`, `core/tenancy/platform_permissions.py` — none in the vault `docs/06-database/schema.sql`, the migration-order doc, or the core module map (same posture as `incidents`/`import_batches`). Also the vault `roles/permissions` catalog description predates the Phase-1 role model (owner/manager/staff/viewer + retired `founder`). Separately, the `support.ticket.raised.v1` outbox event is **not** emitted: like #17, its payload schema must first be registered in the vault's read-only `topics.yaml` (§4, enforced by the drift test); the operator queue reads by poll, so the loop does not need it.
- **Partially resolved (2026-08-12):** (a) **`schema.sql` regenerated** — founder ran `make schema-doc` (pg_dump), so `support_tickets` / `platform_admins` / `platform_access_log` are now in the vault schema. (b) **The event is now emitted** — `support.ticket.raised.v1` (payload `ticket_id`, `priority`, `severity`) registered in `topics.yaml` + vendored `spec/` + `ALLOWED_EVENT_TYPES`; `support.service.raise_ticket` emits it in the same txn (`test_support_api.py::test_owner_raises_ticket_with_defaults` asserts it). **Still deferred (narrative only, low value):** the migration-order doc renumber (the vault's 018–026 plan diverged from the built numbers) + module-map + RBAC-spec touch-ups — to do in a dedicated vault pass; no runtime impact.

### ~~22. Tier engine read `approval_policies` by action only (no pack/org scope)~~ — RESOLVED 2026-08-11 (founder-requested per-vertical separation)

- **Severity (was):** Low for the single-pack MVP; a real cross-pack tier leak once a 2nd active pack coexists. Founder asked to fix it **now** (Boutique vertical + more store owners coming): *"per active pack separation right now… separate per vertical and under that per store owner."*
- **Resolution (2026-08-11):** `core/approvals/engine.py::_contributors` now scopes the policy lookup to the org: a **`core`** rule is platform-wide; a **`pack`** rule applies **only if the org has that pack installed** (`pack_id IN (SELECT … FROM pack_installations WHERE org_id=:org AND status='active')`); a **`tenant`** rule applies only to that org (`org_id = :org`). So one vertical's rules never govern another's runs, and a store owner's own rules stay theirs. Added `test_approval_engine.py::test_pack_rules_scoped_to_the_installed_pack` (a non-installed pack's tier-4 rule does **not** apply → falls back to the default unknown tier). Four fixtures that seeded pack rules without installing the pack (`test_send_loop`, `test_tool_action_bridge_tiers`, `test_approval_engine`, `test_approval_service`) now create an active `pack_installation` — which is also more realistic. This **also removes the old test-isolation coupling** (a leftover pack rule can no longer leak into an org that didn't install that pack). `baseline_tier` (tenant-rule-write validation) still uses the conservative all-packs max — a follow-on only if it ever over-restricts a tenant rule.
- **Still open — separate local-only issue (#22b below):** the pollution failures are unrelated to the tier engine.

### 22b. Local test DB has accumulated install-collision cruft (local-only)

- **Severity:** Low — **local only**; CI is unaffected (fresh Postgres per run; these suites aren't in the CI `test` job).
- **Description:** the shared local dev DB accumulated orphaned install rows from iterative dev (leftover `prompt_layers` / `catalog_schemas` / `pack_installations`), so `test_prompt_activation` (and sometimes `test_rate_ingestion`) fail locally on fresh-state assumptions (`NOT EXISTS`/`ON CONFLICT` guards silently skip re-seeding). Not caused by any shipped change (reproduces with changes stashed).
- **Next action:** a local DB reset (`make down` → `make dev` → `make migrate`) clears it — deferred (destructive; needs founder approval to run `make down`, which drops the dev volume).

### 23. CRM-depth tables land outside the vault (migration 040 — reconciliation)

- **Severity:** Low — documentation/reconciliation only; the DB is correct, migrated (up/down verified), RLS-enforced and isolation-tested. No runtime impact.
- **Owner:** Founder (vault reconciliation) on the next pass.
- **Description:** D2 (CRM notes + tags) adds two org-scoped tables — `customer_notes` + `contact_tags` (**migration 040**) — and a new module `core/customers/annotations.py`, none of which are in the vault `docs/06-database/schema.sql`, the migration-order doc, or the core module map (same posture as `incidents` / support-tickets, BLOCKER #21). Both tables are RLS-enabled (`apply_rls`) with `ON DELETE CASCADE` from `organizations` and `contacts`.
- **Partially resolved (2026-08-12):** **`schema.sql` regenerated** — founder ran `make schema-doc`, so `customer_notes` + `contact_tags` are now in the vault schema. **Still deferred (narrative only):** note migration 040 in the order doc + add `core/customers/annotations` to the module map — dedicated vault pass; no runtime impact.

### ~~24. DPDP erasure retention-exception policy~~ — RESOLVED 2026-08-11 (soft-erase + platform archive)

- **Decision (founder 2026-08-11):** erasure **anonymises + retains** (not hard-delete), and the Growth Operator keeps the original for data requests: *"Growth operator has ultimate powers … who can read the history (even deleted ones) but not store owner … indefinitely … add auto-purge later."*
- **Resolution:** `erase_customer` now soft-erases — it archives the full original record into `erased_customer_archive` (platform-admin-only, **split RLS**: the store owner may INSERT their own during the erase, but only `app.platform_admin='on'` may SELECT), audits `dsr.fulfilled` (no PII), deletes the message content + notes + tags, and anonymises the contact (`full_name`/`phone`/`email`/`attributes` NULL, `erased_at` stamped) while **keeping orders + leads** (revenue/ROI history). The store owner's customer list excludes erased contacts; `GET /v1/admin/erased-customers/{id}` (operator, `platform.tenants:manage`) retrieves the archive. Migration 041. Recorded in DECISIONS. **Deferred:** an auto-purge of archives after a retention window (a later ticket) — kept **indefinitely** for the pilot.

### ~~25. Isolation + integration suites are latently red on `main` — and CI does not run them~~ — RESOLVED 2026-08-11

- **Resolution (2026-08-11, branch `chore/fix-latent-reds-wire-isolation-ci`):** (1) added `erased_customer_archive` to `ALLOWED_CROSS_TENANT_TABLES` in `test_platform_admin_scope` (the security item — its isolation coverage already lives in `test_customer_dpdp.py`); (2) `test_prompt_activation` teardown no longer globally deletes shared pack rows — it now guards on `pack_installations` existence (mirrors the canonical `test_jewelry_install` teardown) and leaves the shared base prompt_layer; (3) `test_rate_ingestion` was pure local pollution (a stale `ibja_gold` rate_source), no code change — passes on a fresh DB; (4) `test_onboarding` + `test_dashboard_overview` fixtures assumed a pack already existed (`SELECT id FROM packs LIMIT 1` → NULL on a fresh DB → `catalog_items.pack_id` NOT-NULL violation) — they now create + clean up a minimal pack. **CI now runs both suites:** the `isolation` job in `.github/workflows/ci.yml` is no longer a placeholder — it stands up Postgres + Redis + `app_rw`, migrates, and runs `pytest tests/isolation` **and** `pytest tests/integration`. Verified on a throwaway scratch DB (the CI scenario): **567 passed, 4 skipped, 0 errors**. This class of latent red is now caught on every push/PR.
- **Severity (was):** Medium — one item security-relevant (cross-tenant guard); the rest local test hygiene. No runtime/data impact. Discovered during CP-2b (verified by stashing CP-2b: all reproduced on clean `main`, so none were caused by CP-2b).
- **Owner:** Engineering.
- **Description:** `.github/workflows/ci.yml` runs only `tests/unit` + `tests/e2e` (plus ruff/mypy/guards/gitleaks). The `isolation` and `evals` jobs are **non-executing placeholders** (they `find`/`echo` only — deferred to MVP-097), and `tests/integration` is never run in CI. So the following have been red locally while `main` still shows "CI green":
  1. **`tests/isolation/test_platform_admin_scope::test_platform_admin_flag_referenced_by_exactly_the_allowlisted_tables`** — *the important one.* The soft-erase migration (041, BLOCKER #24) added an `app.platform_admin` operator-read policy on `erased_customer_archive`, but `ALLOWED_CROSS_TENANT_TABLES` in the guard was never updated. The guard is schema-deterministic — it would fail on **any** migrated DB, i.e. it would fail in CI too *if CI ran it*. The table's isolation coverage already exists (`tests/integration/test_customer_dpdp.py` proves operator-only read). Fix = add `"erased_customer_archive"` to the allowlist.
  2. **`tests/integration/test_rate_ingestion` (3 tests)** — `snapshot_count()` asserts `1`, gets `0` (a snapshot id is returned but the count query sees nothing — RLS/visibility or env quirk). Needs a closer look; likely a test/context bug, possibly local-env-specific.
  3. **`tests/integration/test_prompt_activation` (4 tests)** — test bodies pass; **teardown** raises `prompt_bindings_vertical_layer_fkey` deleting `prompt_layers` while pack-scoped bindings still reference them (fixture cleanup ordering / leftover pack rows). Local pollution-flavoured.
- **Next action (proposed follow-up ticket):** (a) add `erased_customer_archive` to `ALLOWED_CROSS_TENANT_TABLES`; (b) **wire the isolation suite (and ideally integration) into `ci.yml`** so this class of latent red is caught going forward; (c) fix the rate_ingestion + prompt_activation test bugs. Kept out of CP-2b to preserve scope discipline (§6) — none block CP-2b, which is independently CI-green.

### 26. CP-4 follow-ups: per-store cred consumption + operator-console auto-logout

- **Severity:** Low — enhancements, not defects. No runtime/data risk. CP-4 setup is complete + tested.
- **Owner:** Engineering (schedule after the CP sequence, or when go-live nears).
- **Description:** CP-4 lets the operator *store* a store's channel credentials (encrypted). Two
  intentional deferrals: (1) the Instagram/Google **send adapters** (`core/channels/instagram`,
  `core/channels/google_ads`) still read **global** env settings (`instagram_*`, `google_ads_*`) — they
  should load the **per-store** creds from `channel_credentials` at send-time (WhatsApp already does via
  `load_credentials`). Until then the per-store creds are stored but not consumed; sends are
  gated/simulated regardless, so nothing sends. (2) The **operator console (web-ops)** has no
  auto-logout / screen-lock — the CP-4 write-only design stops an unattended session from *reading* a
  key, but it could still be used to replace/remove a channel (every such action is audited). Founder
  raised the "away from my desk" threat when approving CP-4.
- **Next action:** when wiring live sends, switch the IG/Google adapters to per-store `load_credentials`
  (org context required); add an idle-timeout / re-auth to the operator console.
- **CP-5 additions (2026-08-11):** (3) `core/runtime/llm_client.py` uses a single `llm_api_key` +
  `llm_provider` — once stores can pick different providers per agent (CP-5), go-live needs **per-provider
  keys** (operator-held). (4) A per-store model **override** (`org_model_routes`) stores no fallback
  chain, so an overridden route loses the global default's provider failover — add failover for
  overrides when wiring live models. Both are gated/simulated until `llm_provider_enabled`.

### ~~27. LP-2d: vault `tool-permissions.yaml` lands outside the vault (campaigner landing tools)~~ — RESOLVED 2026-08-12

- **Severity:** Low — narrative/doc sync only. **No runtime or test risk** (the drift test reads the
  vendored `spec/agents/tool-permissions.yaml`, which IS updated; the vault original is not test-checked
  — same posture as blockers #17/#21/#23).
- **Description:** LP-2d extended the `campaigner` archetype's level-1 `capability_allowlist` with
  `landing_page.generate` + `landing_page.publish`. Kept in byte-for-byte sync across the three
  test-checked mirrors: `core/packs/archetypes.py` ↔ `spec/agents/tool-permissions.yaml` ↔ the seeded
  `agent_archetypes` row (**migration 047**).
- **Resolution (2026-08-12):** founder edited the vault
  `docs/implementation/agents/tool-permissions.yaml` — the campaigner allowlist now parses to
  `[segments.query, campaigns.execute, templates.read, landing_page.generate, landing_page.publish]`,
  matching the vendored spec + code constant + seeded DB row (verified: `yaml.safe_load` equals the
  target list; the editor reformatted inline→block, which is immaterial — data, not text, is compared).
  Vault is not git-tracked, so the file save is the record.

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

### ~~28. Ghost-recovery workflow: two refinements never re-enabled after the CAPTURE-GAP migration~~ — RESOLVED 2026-08-12

- **Severity:** Medium — this is the **product wedge**, so the gap matters commercially even though
  the core loop runs. No runtime risk: the workflow parses, compiles, and runs gated-simulated today.
- **Surfaced:** 2026-08-12, while answering the founder's question "is ghost recovery even implemented
  or silently set aside?" (it **is** implemented — see the README's new "The wedge" section and the
  MVP-073h/i/j entries; 13 tests pass across unit + integration + RLS isolation).
- **Description:** `verticals/jewelry/workflows/silent_lead_reactivation.yaml` (v4) still carries a
  header comment deferring three things "to the CAPTURE-GAP migrations (needs data today's schema
  lacks; then re-enable)". **Migration 038 subsequently added that data** — `leads.last_customer_msg_at`,
  `leads.last_outbound_msg_at`, `last_message_direction`, `first_customer_response_at` all exist now —
  but the workflow was never updated, so two refinements remain off:
  1. **`classify_ghost`** — distinguishing a genuine ghost from *the shop* stopping replying
     (needs `last_outbound_msg_at` / `last_message_direction`). Without it, a store that dropped the
     ball can be mislabelled as a ghosting customer.
  2. **The 24h post-quote silence window** in the trigger (needs `last_customer_msg_at`). Today the
     trigger fires on `lead.stage.changed == 'quoted'` with no silence dwell, so timing is coarser
     than the spec.
  (The third deferral — writing the owner's pick to `lead_diagnoses` — **is** done: the
  `approval_gate` has `label_sink: lead_diagnoses`.)
- **Resolution (2026-08-12) — delivered by GHOST-1b + GHOST-1c, and the deeper defect they exposed:**
  1. **ghost vs shop-stopped-replying — DONE**, deterministically in `core/customers/recovery.classify`
     rather than as the spec's `classify_ghost` agent step: a customer who spoke last and was never
     answered is `shop_stopped_replying` → **the owner is told, the customer is never chased**. Dropping
     the agent call (no model needed to compare two timestamps) is recorded as a deliberate deviation in
     DECISIONS 2026-08-12. Covered by `test_customer_waiting_on_the_store_is_never_a_ghost` +
     `test_sweep_never_chases_a_customer_waiting_on_the_store`.
  2. **The silence window — DONE and improved.** Not a fixed 24h post-quote dwell but an
     **owner-configurable** threshold (`recovery.silence_hours`, default **72**) measured from the
     **customer's** last message, so a lead that replies and goes quiet again **re-enters** recovery —
     the founder's case, which a fixed post-quote window could not express.
  3. **The stale header — DONE**: `silent_lead_reactivation.yaml`'s CAPTURE-GAP comment now states
     reality instead of deferrals.
  4. **The eval-set extension is moot**: shop-stopped-replying is no longer a diagnosis *reason* to be
     evaluated statistically — it is a deterministic pre-diagnosis classification with direct unit +
     integration tests.
- **Bigger defect this uncovered (also fixed):** the workflow could not fire **at all** — no code
  advanced a lead's stage, nothing emitted the trigger event, and the pack's trigger name could never
  match the routing lookup. Fixed in **GHOST-1a** (`00eedea`), with GHOST-1b (`097c77b`) and GHOST-1c
  (`0f44f7e`) completing the loop.
- **Residual (enhancement, NOT a blocker):** the **sales-handoff branch** — escalating a high-value or
  repeatedly-unrecovered lead to a named human salesperson instead of another message — remains
  unbuilt. Noted in the workflow header; schedule as a normal ticket if the pilot wants it.

### ~~29. Vault `topics.yaml` lands outside the vault (two lead event lines)~~ — RESOLVED 2026-08-12

- **Severity:** Low — narrative/doc sync only. **No runtime or test risk** (the drift tests read the
  vendored `spec/events/topics.yaml`, which IS updated; the vault original is not test-checked — same
  posture as #17/#21/#23/#27).
- **Description:** GHOST-1a widened `lead.stage_changed.v1`'s `last_customer_msg_at` from `rfc3339` to
  **`rfc3339|null`** (founder-approved) so a lead with no customer message yet — e.g. captured from a
  landing form — can still emit the transition that starts ghost recovery. Updated in
  `spec/events/topics.yaml` + regenerated `core/events/types.py`; the vault original still says
  `rfc3339`.
- **Second line (GHOST-1b):** a **new** event `lead.went_silent.v1` was added — it is what actually
  starts ghost recovery once the daily sweep detects silence. Registered in
  `spec/events/topics.yaml`, `core/events/topics.py` (ALLOWED_EVENT_TYPES) and the regenerated
  `core/events/types.py`; the vault original has neither change.
- **Resolution (2026-08-12):** the founder made both edits in the vault
  `docs/implementation/events/topics.yaml`. **Verified by parsing both files and comparing the
  payload dicts** — `lead.stage_changed.v1` is
  `{lead_id: uuid, stage: string, last_customer_msg_at: rfc3339|null}` and `lead.went_silent.v1` is
  `{lead_id: uuid, stage: string, silence_hours: int, last_customer_msg_at: rfc3339|null}`, both
  **identical** to `spec/events/topics.yaml`. The vault, the vendored spec, the hand-maintained
  `ALLOWED_EVENT_TYPES` and the generated `core/events/types.py` now all agree. Vault is not
  git-tracked, so the file save is the record.


---

## #22c — Whole-tree `pytest tests` interference (local only, pre-existing)

**Opened** 2026-08-13 (PLAN-1). Running the entire `tests/` tree in **one** pytest process yields 3
failures (`test_rate_ingestion`) and 8 teardown errors (`test_prompt_activation`, `test_settings`).

**Not a product defect and not caused by PLAN-1:** reproduced identically on `main` @ `a4f2e09` in a
clean git worktree with none of the PLAN-1 changes. Running the same tests with CI's scoping
(`tests/isolation`, `tests/integration`, `tests/contract`) on a fresh DB gives **698 passed, 0
failed**. CI runs each directory as a separate job, so it never encounters this.

**Cause (suspected):** cross-directory state leakage — engine/sessionmaker caches and org-scoped
fixture teardown when unit/e2e modules share a process with the DB-backed suites.

**Impact:** developer ergonomics only. `make test` / whole-tree runs are misleading.
**Next step:** isolate the leaking fixture. Not scheduled; no ticket assigned.


---

## #30 — Plan reassignment does not reconcile agent instances (**PLAN-5 P0**)

**Opened** 2026-08-13 (found during the PLAN-2 audit; confirmed by founder review).

`core/billing/service.py::assign_subscription()` cancels the old subscription and inserts the new
one, touching **only** `billing_subscriptions`. `core/tenancy/provisioning.py::activate_plan_agents()`
— the CP-2b mechanism that sets `agent_instances.status='active'` for the agents a plan switches on
— is invoked **solely on the provisioning path**.

**Consequence:** downgrading a store to a plan with fewer agents leaves the removed agent's instance
active. The agent keeps running despite no longer being sold.

**Not fixed in PLAN-2** — deliberately, to avoid mixing resolver work with enforcement. PLAN-2
resolves commercial truth only and does not change any activation behaviour.

**Requirement (PLAN-5 P0):** plan reassignment must reconcile/deactivate agent instances no longer
included by the new plan. **No live plan-switching rollout may occur before this is closed.**
Impact today is nil: 0 active subscriptions and 0 agent instances exist.


---

## #31 — Audit correction: earlier "0 subscriptions" figures were RLS-masked (closed)

**Opened and closed** 2026-08-13 (PLAN-3).

The PLAN-1 and PLAN-2 reports stated *"0 subscriptions, 0 pack installations, 0 agent instances."*
Those queries ran against `database_url` (`app_rw`), which is RLS-bound with no org context, so they
returned **masked** results rather than empty ones. Queried with a `rolbypassrls` connection the dev
database actually held **5 active subscriptions, 5 active + 2 failed pack installations, and 16
paused + 8 active agent instances**.

**This changes historical audit facts, not implemented contracts.** PLAN-1's legacy-key audit read
`billing_plans`, which has no RLS and was accurate; PLAN-2's resolver behaves identically at any
count. Neither ticket's correctness is affected.

**Fixed forward:** `core/billing/presets.py::assert_global_visibility()` makes this class of error
impossible for the seeder — it aborts unless the connection holds `rolbypassrls` or superuser, so a
sold/unsold decision can never be made behind row-level security. Any future operational audit of
tenant-scoped tables must use a privileged connection.


---

## #32 — Sold plans could be rewritten in place (RESOLVED by PLAN-4)

**Opened and resolved** 2026-08-13.

PLAN-3 locked canonical preset rows, but every other plan stayed mutable. Demonstrated against a
real custom structured plan with an **active** subscriber: a single `update_plan()` call moved price
₹5,000 → ₹0.01, emptied `config.entitlements` and zeroed the seat limits. No new row, no version, no
record of the prior terms — the subscriber's purchased terms and their runtime entitlements both
changed silently.

Operator-authenticated and platform-permission gated, so not a security hole, but a **commercial-
history integrity defect**.

**Resolved:** `SoldPlanImmutable` locks every commercial and presentational field once any
subscription of any status has referenced a plan; only `active` remains editable. The check uses the
SECURITY DEFINER primitive from migration 051 (an RLS-scoped session would have answered "never
sold" for every plan), and runs **after** a `FOR UPDATE` lock on the plan row so it cannot race with
`assign_subscription()`.

**Related fix:** `assign_subscription()` did not validate `billing_plans.active`, so a retired plan
could still be assigned — and it cancelled the store's current subscription *before* touching the
target, meaning a failed assignment left the store with no plan. Both corrected.


---

## #30 — Plan reassignment did not reconcile agent instances (RESOLVED by PLAN-5)

**Resolved** 2026-08-13, though not the way the blocker originally framed it.

Reconciliation now runs inside `assign_subscription()`, but it is **cleanup, not the security
boundary**. The boundary is `assert_agent_executable`, called from `_drive()` — where `start_run`,
`resume_run` and `resume_after_approval` all converge — and again at the mediation proxy. Commercial
authority is therefore evaluated at execution time, so a downgraded agent cannot act even while its
`agent_instances` row still says `active`, and a delayed or failed reconciliation cannot widen
authority.

Crucially, reconciliation **does not rewrite operational status**: doing so would make an operator's
manual pause indistinguishable from a commercial removal and silently reactivate it on re-upgrade.

**The rollout caveat in this blocker is lifted:** live plan-switching no longer risks an agent
outliving its plan.

---

## #33 — Mediation had no entitlement check (RESOLVED by PLAN-5)

**Opened and resolved** 2026-08-13, found during the PLAN-5 audit and rated the ticket's
highest-severity gap by the founder.

The mediation chain ran manifest → params → rate limit → budget → tier → audit → execute with **no
commercial check anywhere**. `landing_page.generate` and `landing_page.publish` are in `REGISTRY`, so
an agent could generate and publish a landing page for a tenant whose plan excluded
`landing_pages` — bypassing the HTTP gate entirely and making the route-level coverage misleading.

**Resolved:** the proxy re-checks current agent authority *and* the tool's mapped capability
immediately before execute, returning a structured refusal (never a crash) and auditing the denial.
Every `REGISTRY` tool must now declare `capability_key` or `plan_exempt_reason`, enforced by a CI
guard with a mutation test, so a future tool cannot re-open this path.

---

## #34 — recovery_sweep processed every organization (RESOLVED by PLAN-5)

**Opened and resolved** 2026-08-13. The daily sweep iterated `SELECT id FROM organizations`, so
unsubscribed and cancelled stores still received ghost-recovery business processing. It now skips
orgs without `ghost_recovery`; their existing lead history stays readable per the data-continuity
ruling.

## #35 — `negotiating` is not a lead stage the CRM can store (NON-BLOCKING — post-pilot)

**Opened** 2026-08-13 (PILOT-1C). Recovery was to trigger on `quoted` **and** `negotiating`, but
`leads.stage` permits only `new / qualified / quoted / visit_booked / won / lost`. `ENGAGED_STAGES`
had named `negotiating` and `contacted` since GHOST-1b; neither exists, so two of the three entries
selected nothing and the sweep only ever matched `quoted`. It now names only `quoted` — the truth of
what ran — with a unit test asserting every entry against the migration's CHECK constraint.

**Founder ruling 2026-08-13:** `ENGAGED_STAGES = ("quoted",)` is ACCEPTED for Pilot-1. `quoted` is
the minimum defensible recovery wedge because the system holds concrete evidence that the merchant
made a commercial response to the customer. `negotiating` is recorded as later CRM/product-depth
work and is explicitly **not** a Pilot-1C blocker.

## #36 — Three `test_rate_ingestion` failures are a LOCAL dev-database artifact (OPEN — low)

**Opened** 2026-08-13. **Refined during post-merge verification.**

`test_fetch_writes_snapshot_and_publishes_updated`, `test_out_of_bounds_quarantined_no_snapshot_and_alert`
and `test_manual_entry_writes_snapshot_and_audits` fail with `snapshot_count() == 0` on the local
development database. First established as pre-existing by checking out `main` and re-running.

They then **passed on a freshly migrated database** with the CI role split (`app_rw` runtime,
`growth_operator` migrator), and CI itself is green on them. So this is not a code defect: the tests
carry an assumption about database state that a long-lived development database eventually violates.

That is worth its own small ticket — a test that only passes on a clean database will eventually
fail for everyone and be diagnosed as a real regression. It is **not** a PILOT-1C blocker and must
not be attributed to it.

**Exact mechanism identified 2026-08-16** (during PILOT-1D-L; re-confirmed pre-existing by stashing
the branch and re-running). The `scene` fixture inserts a fresh `rate_sources` row with
`source_key='ibja_gold'` on every run and never removes it, while the product resolves a source *by
`source_key`*. On the founder's database one leftover row from an earlier run already exists — it
currently carries **87** accumulated `rate_snapshots` — so during the test the key matches two rows
and the product writes against the stale one. `scene.snapshot_count()` filters on the fixture's own
`source_id` and therefore sees 0.

This also explains the second failure's shape: with no snapshot on the resolved source there is no
baseline to compare against, so an out-of-bounds value is classified `updated` rather than
`quarantined`. All three failures are one cause, not three.

Not RLS and not a DSN split — both were checked and ruled out: `rate_snapshots` has neither RLS nor
an `org_id` column, and `database_url` and `database_migrator_url` point at the same database.

The fix belongs with #43 (TEST-DB-ISOLATION): the fixture should scope resolution to the source it
created, or run against a per-test database. Deliberately **not** fixed inside PILOT-1D-L, which is
scoped to the two runtime defects.

## #37 — A real message has never physically reached a phone (RESOLVED 2026-08-17 — PHYSICALLY PROVEN)

**Closed 2026-08-17.** A real Priya reply reached the founder's handset and was confirmed visually.
Meta independently reported `sent → delivered → read`. Full evidence in the PILOT-1D-L record at the
end of this file. The original open text follows for history.

---


**Opened** 2026-08-13 (PILOT-1C). The recovery slice is proven end to end against real Postgres up
to the provider boundary: gates, guards, RLS, the durable dispatch claim, the lifecycle and the
reply correlation all run against the database. What has **not** happened is an actual WhatsApp
message being delivered to a real handset, because that requires Meta credentials and a real
external side effect, which CLAUDE.md §10.4 reserves to the founder.

This is the difference between **code-complete** and **live-proven**, and it should not be described
either way by accident.

**Founder ruling 2026-08-13:** PILOT-1C closes as **CODE-COMPLETE** only. It is **not** physically
live-proven until PILOT-1D/1E exercises all six of: real Meta/WABA, real approved template, real
handset, real provider credential, real delivery receipt, real customer reply. Expected to close
there. Blocks real-pilot acceptance until then.

## #41 — Deploy guard used `uv` without installing it (RESOLVED 2026-08-14)

**Opened and resolved** 2026-08-14. The new `guard` job in `deploy-staging.yml` ran
`uv run python -c ...` with no `setup-uv` step, so it failed with `uv: command not found` on every
push to main — and unlike the old workflow, which skipped entirely when `STAGING_ENABLED` was unset,
the guard runs unconditionally by design. The founder's inbox found it before any test did.

Nothing was wrong with what the guard checks; it never got to check anything. That is the worst
failure mode for a safeguard: a red build that looks exactly like the violation it exists to detect.

**Resolution:** the guard uses `python3` and the standard library only (merge `3215b38`). Two tests
added — one asserts no workflow job anywhere uses `uv` without installing it, the other pins the
guard to the standard library. Both catch a class of bug that passes locally, where `uv` is always
on PATH.

## #38 — Model registry drift is only caught by hand (OPEN — low, recurring)

**Opened** 2026-08-13 (PILOT-1A). Two vendors retired four models while this repository kept
offering them, and one earlier migration "fixed" the ids without noticing they were gone. A model id
is a fact about someone else's live service; nothing in CI can verify it without making paid calls,
which is correctly out of scope for CI.

Mitigations shipped: `RETIRED_MODEL_IDS` refuses known-dead ids, `current_models()` separates
current from deprecated, and a test refuses any retired id appearing anywhere in `core/`, `scripts/`
or `verticals/`. None of that detects the *next* retirement.

**Next action:** re-verify the registry against vendor documentation before each live activation,
and record the check date in the registry docstring (it now carries one). Consider a quarterly
reminder. Not automatable without paid calls.

## #39 — `evals` CI job is a placeholder echo (NON-BLOCKING — CI hardening)

**Opened** 2026-08-13, per founder ruling. `.github/workflows/ci.yml` runs
`echo "eval harness not wired yet"`. The real harness (`scripts/eval_models.py`) exists and runs
mocked by default, `--live` for real paid calls. Live vendor evals must stay OUT of normal CI.

Explicitly **not** on the critical path. Recorded so the green `evals` badge is not mistaken for
evidence that evaluations run.

## #40 — Production hosting is provisioned by hand (ACCEPTED — by design for Pilot-1)

**Opened** 2026-08-13. Founder ruling: DigitalOcean BLR1, 2 vCPU / 4 GiB, manually provisioned, with
repository-controlled Docker Compose deployment. Terraform is deliberately **not** rewritten to
provision one pilot host; IaC returns when a second reproducible environment is needed.

Consequence to accept knowingly: the host's own configuration (firewall, SSH, age key placement,
cron) is not reproducible from this repository. Documented in `secrets/README.md` and
`infra/db/BACKUP_RESTORE.md`. Supersedes the Hetzner assumption in #10 — Hetzner has no India
region, so `infra/terraform/staging/` remains un-applied scaffolding.

## #42 — `submit_template()` has no product caller (OPEN — blocks PILOT-1D-R)

**Opened** 2026-08-14 (DEMO-UX-1 audit, founder-recorded as a PILOT-1D-R blocker).

The WhatsApp template lifecycle is complete in code except for the step that starts it:

```
pack install  -> message_templates (provider_status='draft')
submit_template() -> Meta            <-- NO CALLER ANYWHERE
Meta status webhook -> apply_status_update() -> 'approved'
GET /v1/channels/whatsapp/templates -> merchant campaign UI (filters on 'approved')
```

`submit_template()` exists and is correct; nothing invokes it. So no template can ever reach
`approved`, and the merchant's campaign selector is empty by construction. That is not a UI defect —
DEMO-UX-1 made the empty state explicit rather than papering over it.

**Required before the real ghost-recovery pilot:** an operator can review a seeded draft → submit it
to Meta explicitly → observe pending/approved/rejected → the merchant's campaign UI sees it.

**Deliberately not built in DEMO-UX-1.** This is the smallest operator submission surface, not a
template design suite, and it must not be built by guessing — it needs the real Meta account.
No Meta call was made.

## #43 — Tests share the founder's development database (**STILL OPEN** — technical debt: TEST-DB-ISOLATION)

> **2026-08-17 — explicitly NOT closed by the PILOT-1D-L merge.** The specific `approval_policies`
> corruption incident below is contained: the offending UPDATE is pack-scoped, a `test-policy-writes`
> guard fails any unscoped policy write in `tests/`, and Ratna's rows were repaired. **General
> shared-test-database isolation remains unsolved** — a test calling `install()` on
> `verticals/jewelry` still receives the live pack id via `ON CONFLICT (slug, version)`, tests still
> resolve production rows by slug, and `test_prompt_activation` still disables the `audit_log`
> immutability trigger. Do not read the containment as a resolution.


### Confirmed incident 2026-08-16 — a test corrupted a live pilot store, and it cost a physical proof

This stopped being theoretical. `tests/integration/test_send_loop.py` contained:

```sql
UPDATE approval_policies SET tier=2 WHERE action_type='action.message.send'
```

Correct for its own fixture; unbounded against the shared development database. `approval_policies`
holds **global pack rows with `org_id` NULL**, so the statement reached every pack in the database —
including Ratna's live jewelry pack `e420d84d-4e3a-4827-842f-8e1a1edcc6c1`. It moved three ordinary
outbound-message rules from tier 1 to tier 2:

| rule | intended | after the test |
|---|---|---|
| Replies in an active customer chat | 1 | 2 |
| Follow-up nudges | 1 | 2 |
| Support replies | 1 | 2 |

**Consequence:** at 18:48:53 UTC a real customer's WhatsApp greeting reached Priya, DeepSeek produced
a correct reply, and the send parked on approval `122fea42-fe6f-44b7-8a22-0c5262cb6f4c` instead of
going out. The physical proof was lost to a test.

**What was actually wrong, and what was not.** The pack source has said tier 1 in every commit since
the initial scaffold; the parser produced tier 1; `installer._seed_policies` writes `rule.tier`
faithfully; `agent_bindings.tier_defaults` for the same pack still held tier 1 throughout. **Only the
`approval_policies` rows were corrupted.** Nothing in the repository ever expressed tier 2 for these
rules — which is why reading the source could not explain the database, and why this looked for some
time like a pack or installer defect.

**A second door into the same room.** `installer._get_or_create_pack` upserts on `(slug, version)`,
so any test calling `install()` on `verticals/jewelry` receives the **real** pack id rather than a
fixture's. `tests/integration/test_prompt_activation.py:61` does exactly that, and its teardown then
*deliberately* leaves the shared rows behind when another org still has the pack installed — which
Ratna does. That is how these rows came to be re-created at 18:21:02 with no `pack.installed` audit
(the test also deletes its own audit rows). Tests write into live pilot pack rows as a matter of
course; the unscoped UPDATE is what made it visible.

**Fixed in this patch (narrow):** the UPDATE is scoped by `pack_id`, and a new `test-policy-writes`
guard (`scripts/guards.py`) fails any UPDATE/DELETE of `approval_policies` in `tests/` that names
neither `pack_id` nor `org_id`. `tests/unit/test_jewelry_policy_baseline.py` pins the source
baseline. Ratna's three rows were repaired by a single targeted UPDATE (3 rows).

**NOT fixed, and why #43 stays OPEN:** the guard stops one syntactic class of accident. It does not
stop a test from installing into the live pack, from deleting shared rows, or from writing any other
table. The real fix is an ephemeral per-run database, which is not built here.

**Related test-isolation debt found while investigating:**

- `tests/integration/test_prompt_activation.py` runs
  `ALTER TABLE audit_log DISABLE TRIGGER trg_audit_log_immutable` on the shared database, disabling
  an append-only integrity control for the duration of a test run.
- Several fixtures resolve production rows by name (`SELECT id FROM packs WHERE slug='jewelry'`)
  rather than creating their own.


**Opened** 2026-08-14 (DEMO-UX-1, founder-recorded).

`pytest` runs against the same Postgres the founder develops against. Fixtures that leaked rows
therefore polluted a working environment — 1171 billing plans and 189 organizations, which made the
operator console's plan list unusable and is the likely cause of BLOCKERS #36 (rate-ingestion tests
that fail on the dev database and pass on a fresh one).

DEMO-UX-1 fixed the leak at its source and made cleanup ownership-based, so the suite no longer
accumulates. It did **not** fix the sharing, which is the underlying condition.

**Desired invariant:** development database ≠ test database, or tests run against a disposable
ephemeral database (a template database cloned per session, or a container per run).

**Not implemented here** by founder instruction: the current fix is safe on its own, so the larger
refactor does not need to ride along with a demo-polish ticket. Worth doing before the test suite
grows further — every fixture written in the meantime is written against the wrong assumption.

## #44 — MINIO/OFFSITE-MEDIA-BACKUP (OPEN — blocks PILOT-1E production acceptance)

**Opened** 2026-08-14 (DEMO-UX-1 final review, founder-recorded).

`scripts/backup-nightly.sh` dumps PostgreSQL, encrypts it and ships it off-site. Catalog product
images do **not** live in PostgreSQL — after DEMO-UX-1 the originals and both derivatives live in
MinIO on the droplet, on a Docker volume with no backup at all.

So a restored database would come back pointing at object keys whose bytes were lost with the host.
The catalog would look intact and every product photograph would be a broken image, which is a
worse failure than an obvious one: nothing would alert, and the merchant would find out from a
customer.

**Not implemented here** by founder instruction — this correction round was scoped to the review
findings, and bolting a second backup subsystem onto it would have widened it well past that.

**Required before PILOT-1E production acceptance.** Likely the smallest form: `mc mirror` (or
`aws s3 sync`) of the bucket into the same encrypted off-site destination the database dump already
uses, on the same nightly schedule, with the restore drill extended to prove an image comes back.

## #45 — An agent run reports `status=succeeded` when its send was denied (OPEN — correctness of run status)

**Opened** 2026-08-16 during PILOT-1D-L, at founder request. **Recorded, not fixed** — outside the
two-defect scope of this patch.

In the live run that exposed the PILOT-1D-L defects, `messages.send` was **denied by manifest
integrity** and no message was produced, yet the agent run finished with `status=succeeded`. The run
status currently reflects "the graph reached its terminal node without raising", not "the run
achieved its purpose".

Why it matters more than it looks: run status is what an operator scans, and what any future alerting
would key on. A denied send is exactly the event that must not be invisible — it is the difference
between "the customer was answered" and "the customer was silently not answered". During a pilot this
would read as a healthy run.

Deliberately **not** addressed here: changing terminal-status semantics affects every consumer of run
status and needs its own ticket and its own tests, and folding it into a defect patch is how an
unrelated behaviour change ships unreviewed.

## #46 — Stale CP-5 `priya.reason` tunable-node mismatch (OPEN — carried, untouched)

**Recorded** 2026-08-16. Founder explicitly excluded this from PILOT-1D-L ("do not work on the stale
CP-5 `priya.reason` tunable-node mismatch in this patch"). Logged so it is not lost now that the
routing path around it has been touched — the PILOT-1D-L change corrects *what is sent* to the
provider and does not alter node naming or tunables.

## #47 — WhatsApp webhook normalizer had no production caller (RESOLVED 2026-08-16 — PHYSICALLY PROVEN 2026-08-17)

**Physically proven 2026-08-17.** Webhook `fabc6e5b` was normalized automatically by the worker-owned
loop with no manual `normalize_pending()` call, producing `msg.received.v1` `a254f4de` 0.9s after
arrival. See the PILOT-1D-L record at the end of this file.


**Opened and resolved** 2026-08-16 during PILOT-1D-L physical Meta proof.

`core/channels/whatsapp/normalizer.py::normalize_pending()` was complete and correct — contacts,
conversations, inbound messages, `msg.received.v1`, delivery statuses, STOP handling — and **nothing
in production ever called it**. A grep for callers found the function's own module and the tests,
and nothing else. The automatic live path therefore ended one step in:

    real handset → Meta → Cloudflare → FastAPI → webhook_events → *stop*

A real customer message sat in Postgres until someone ran the function by hand, which is exactly
what we had been doing without registering it as a gap.

**Why a full green suite never caught it.** Every normalizer test called `normalize_pending()`
itself. Each one supplied the missing production caller as part of its own arrangement, so the suite
proved the function worked and could not, even in principle, notice that no one ran it. Physical
ingress was the first thing that did not bring its own caller.

**Fixed** by a worker-owned loop, `run_normalizer()`, started by `run_worker()` alongside the outbox
publisher and the consumers — no new process, no new service. Making it multi-worker-safe required
correcting how a webhook is claimed: the previous shape selected pending rows in one session and
processed them in another, so adding `FOR UPDATE` to that select would have locked nothing (the
lock dies with the session that took it, before any work happens). Candidate discovery now takes no
locks at all, and each candidate is re-checked and locked with `FOR UPDATE SKIP LOCKED` inside the
transaction that normalizes it and writes `processed_at`.

No migration and no new column. `processed_at` is deliberately **not** pre-set to reserve a row:
that would mean a crash mid-normalization left a webhook marked done that never was, silently
discarding a real customer message. A failure rolls back, the lock releases, and the webhook stays
pending.

**Resolved on evidence:** `tests/integration/test_whatsapp_normalizer_orchestration.py` (8 tests)
drives the loop rather than the function, and both concurrency tests were confirmed to fail against
unlocked code and pass against the fix (5 runs each way).

**Still unproven, deliberately:** the end-to-end physical chain. This closes the code gap; the live
proof is a separate act — a new message from the founder handset with the real worker running and
no manual invocation. Until that happens #37 stays open.

## #48 — Priya inference mode / deadline collision exposed by physical WhatsApp (RESOLVED 2026-08-17 — PHYSICALLY PROVEN)

**Physically proven 2026-08-17.** Run `c63ca8d5` completed a DeepSeek turn in **1388ms** against a
20s attempt budget inside a 45s node deadline, with thinking explicitly disabled, and the run
finished `succeeded` in 3.1s. The two hypotheses recorded below were never confirmed and are left
as written — the fix stands on the two code-level defects, both now exercised live.


**Opened** 2026-08-16 during PILOT-1D-L physical proof, immediately after #47 made the path
automatic.

**What physically happened.** A new real handset message — "Hi, can someone help me?" — traversed
the whole chain automatically with no manual `normalize_pending()` call: handset → Meta → webhook →
`webhook_events` → the new normalizer → `msg.received.v1` → outbox → Redis → planner → Priya run →
route → compose. The worker log then showed `POST https://api.deepseek.com/v1/chat/completions`
returning `HTTP/1.1 200 OK`, and the run nevertheless ended:

    run     e1710705-997d-41fe-abaf-04087724b9e6
    status  interrupted
    error   {"code": "provider_unavailable", "detail": "model_turn timeout"}

Duration ~30s. Only two `agent_steps` persisted (`route`, `compose`) — no `model_turn`. No
`costs_lite` row for the run. The run state correctly carried `body`, `task=qualify`,
`intent=greeting`, `clarify=false`, so the earlier runtime-input-loss defect is confirmed fixed.

**Two proven defects.**

1. *Implicit provider reasoning mode.* The adapter sent only `model`, `max_tokens` and `messages`.
   DeepSeek V4 defaults thinking ON, so the customer-facing concierge turn was being asked to
   deliberate over a greeting, with no explicit control either way.
2. *Equal inner and outer deadlines.* `llm_client.call_provider(timeout=30.0)` sat inside
   `executor.NODE_TIMEOUT_S = 30.0`. A provider may consume the entire enclosing budget, leaving
   nothing for route lookup, parsing, adapter normalization, cost telemetry or fallback
   bookkeeping — so a successful provider response can be discarded by the deadline meant to
   protect the turn. (Not the executor's step checkpoint: that is written after `asyncio.wait_for`
   returns and is outside this bound entirely.)

**Explicitly hypotheses, not findings.** It is *not* established that thinking mode caused this
particular timeout, and it is *not* established that `_log_cost` hung. The absent `costs_lite` row
is consistent with several stories (the node deadline firing before telemetry ran, telemetry itself
being slow, or the attempt never returning) and the evidence does not choose between them. Two separate logs now exist — one
emitted the moment `provider.complete` returns, one after `_log_cost` completes — so the next
occurrence distinguishes them by which lines are present: both (failure is later), provider only
(the telemetry/cancellation boundary), or neither (the provider call did not return).

**Fixed by** a platform-controlled `core/runtime/inference_policy.py`: a provider-neutral
`ReasoningMode` per node (`priya.reason → OFF`, everything else `DEFAULT`), translated to a vendor
wire field by the selected provider's adapter via a declared `ProviderDefinition.reasoning_control`
— so DeepSeek receives `{"thinking": {"type": "disabled"}}` and OpenAI, which shares the same
adapter, receives nothing. Deadlines are now derived rather than independently written:
`PROVIDER_ATTEMPT_TIMEOUT_S = 20.0`, `MODEL_NODE_TIMEOUT_S = 20 × 2 + 5 = 45.0`, with the model node
alone taking the larger budget.

No route params, admin API or tenant-writable row can contribute a request-body field;
`models_admin` still exposes provider and model only.

**CODE-RESOLVED on evidence:** `tests/unit/test_inference_policy.py` (19 tests), confirmed to fail
against both pre-fix states — deadlines set back to 30/30 (4 failures) and DeepSeek's
`reasoning_control` removed (3 failures).

**#37 stays OPEN.** No real handset message has yet returned a real Vaylorn-generated WhatsApp
reply. This closes the code defects; it does not close the physical proof.

## #49 — `_seed_policies` never updates a policy whose source tier changed (OPEN — upgrade defect)

**Recorded** 2026-08-16 during the #43 incident investigation. **Not causal there** — those rows were
freshly inserted — and deliberately not fixed in that patch.

`core/packs/installer.py::_seed_policies` inserts with
`WHERE NOT EXISTS (... pack_id, scope, action_type, description)`. The guard does not consider
`tier`, `cel_expr`, `approver_chain` or `timeout_s`, so editing any of them in a pack's
`bindings.yaml` and reinstalling leaves the old row in place. The install reports success and the
database silently keeps the previous policy.

This matters most for the case it is least visible in: lowering a tier in source would appear to
work everywhere except the database that enforces it. Needs its own ticket — an upsert has to decide
what happens to an operator's deliberate override, which is a product question, not a SQL one.

## #50 — Approval rows lose `matched_rules` (OPEN — observability debt)

**Recorded** 2026-08-16 at founder request. **Not causal** in the #43 incident and deliberately not
fixed there.

`core/mediation/proxy.py::_engine_tier()` calls `evaluate_tool(...)` and returns only
`decision.tier`, discarding `decision.matched_rules`. Every approval therefore persists
`matched_rules = []`.

Why it is worth fixing: during the #43 investigation the empty list read as evidence that *no policy
matched*, which pointed at pack visibility, RLS and pack-id mismatches — all of which were fine. An
approval that recorded which rule parked it would have identified the corrupted row immediately
instead of after tracing the whole install path. The cost of this debt is measured in wrong turns
during an incident, not in wrong behaviour.

## #51 — POLICY-PREFLIGHT: determine action authority before expensive model inference (OPEN — future architecture/product ticket, NOT scheduled)

**Recorded** 2026-08-16 at founder request. **Deliberately not implemented.** Revisit only after
PILOT-1D-L proves the full physical loop: real handset → Meta → Vaylorn → Priya → real model →
`messages.send` → Meta → real handset reply.

### Problem

The normal execution order today is:

```
event → LLM inference → proposed tool/action → approval policy evaluation → possible Tier 2/3 parking
```

When an action is *deterministically* known to need approval before anything is generated, the
tokens spent producing that content are wasted whenever the owner rejects it.

Where authority is knowable in advance, and where it is not:

| case | knowable before generation? |
|---|---|
| ordinary customer reply (`messages.send`) | yes — usually Tier 1 |
| quote ≥ ₹1,00,000 | yes — the structured amount already decides Tier 2 |
| any discount | yes — structured quote data establishes Tier 2 |
| campaign / broadcast (`action.campaign.execute`) | yes — Tier 3 regardless of the copy |
| angry / legal / refund escalation | **no** — the tier depends on semantic classification of content that does not exist yet |

That last row is why preflight cannot be a simple pre-computation of the final answer.

### Design principle

Decide as much authority as possible **before** spending model tokens, while keeping the existing
final mediation/policy evaluation **after** the model proposes the actual action:

```
event / structured intent
  → deterministic context
  → POLICY PREFLIGHT
  → optionally obtain Tier 2/3 approval first
  → model generation
  → FINAL POLICY CHECK on the actual generated action + parameters
  → side effect
```

### Preflight result shape (illustrative — do not freeze an API from this)

A single integer tier is the wrong return type, because it cannot express "I do not yet know". The
result needs to carry at least: `minimum_tier`, whether the answer is `exact` given current
information, whether generated content still `requires_content_evaluation`, the matched
policy/`reason`, and the action family.

```
PolicyPreflight: action=messages.send    minimum_tier=1  exact=true
                 requires_content_evaluation=false  reason=reply_standard

PolicyPreflight: action=messages.send    minimum_tier=1  exact=false
                 requires_content_evaluation=true
                 reason="generated content could trigger escalation"

PolicyPreflight: action=campaign.execute minimum_tier=3  exact=true
                 requires_content_evaluation=false  reason=any_broadcast
```

### Safety property (the part that must not be got wrong)

**Preflight is an optimization and an early-authorization mechanism. Final mediation remains
authoritative.** A preflight answer of Tier 1 is a prediction, never a grant.

Worked example: preflight expects an ordinary Tier-1 reply → the model unexpectedly proposes a
discount → the final policy engine raises the actual action to Tier 2 → it **must not** auto-send
merely because preflight said Tier 1.

Preflight must never become an authorization bypass. Any implementation that lets a preflight result
substitute for the final check has reintroduced the exact hazard the approval engine exists to
prevent.

### Cost model

The saving is **not** on ordinary Tier-1 replies — those still need the model to produce the reply.
It comes from Tier-2/Tier-3 work that can be rejected *before* generation:

- campaign requested → Tier 3 known deterministically → owner rejects → **zero** campaign-copy
  inference spent
- ₹140,000 quote with 5% discount → structured pricing establishes Tier 2 → a deterministic approval
  card is shown first → only after approval does the model produce polished customer wording

### Non-goals

Do not, under this ticket: redesign approvals, change Tier semantics, change jewelry policy values,
change the Priya runtime, touch #43/#49/#50, delay the PILOT-1D-L physical proof, or introduce
provider-specific logic. This stays provider-neutral.

### Open questions for whoever picks this up

- What does the owner see for a pre-generation approval? A deterministic card built from structured
  data has no generated copy in it, which is a different (and possibly better) review surface than
  today's "approve this draft".
- Does an early approval remain valid once the model produces content that changes the action's
  shape? Probably not — which suggests the early approval authorizes an *intent*, and the final check
  still authorizes the *action*.
- Preflight needs the same policy inputs as the engine. Sharing that evaluation path is what stops
  the two drifting apart and quietly disagreeing.

## #52 — APPROVAL-PARK OBSERVABILITY: log successful run parking at the mediation gate (OPEN — observability only)

**Recorded** 2026-08-16. **Not implemented.** Not causal to any physical test — the runtime behaved
correctly in every observed case; only the record of it was silent.

### Problem

A real Priya execution went event → Priya → DeepSeek → planner → `messages.send` policy evaluation →
Tier-2 approval park, entirely correctly. The worker log ended at:

```
INFO:core.runtime.planner:planner: routed intent=greeting -> concierge/qualify
```

and said nothing further. Nothing recorded that the run had **deliberately** parked.

A healthy approval park is therefore visually indistinguishable from a worker crash, a runtime hang,
a mediation failure, or a dispatch failure. All five produce the same thing in the log: silence after
the planner line. Establishing which had actually happened took database forensics across
`agent_runs`, `agent_steps`, `approvals`, `event_outbox`, `messages` and a read-only re-run of the
approval engine — to confirm the system had done exactly the right thing.

That is the cost being paid here: the most common *correct* outcome of the mediation gate is the one
the log cannot distinguish from a failure.

### Desired behaviour

One structured INFO line at the point a run intentionally parks, carrying safe identifiers only:

```
run parked for approval: run_id=… approval_id=… tool=… tier=…
```

Optionally the action family or a reason category, if it can be obtained safely at that point.

**Never log:** message bodies, prompts, model output, phone numbers, access tokens, secrets, or raw
approval payloads. The line exists so an operator can tell "parked" from "died" — it needs
identifiers to look things up with, not content.

### Scope boundary

Observability only. This ticket must not alter approval semantics, tier computation, quiet hours,
the autonomy overlay, #50 `matched_rules` behaviour, or anything about how `messages.send` executes.
If implementing it requires changing any of those, that is a different ticket.

---

### PILOT-1D-L evidence note — 2026-08-16

The latest real execution (run `a46f2d71-4c66-4b58-9d6d-0d1e7c6dda31`, event
`8ba85018-9acc-4bf7-be15-3652a4f363c2`) proved:

- the repaired jewelry reply rules resolve **Tier 1** — all three matched as contributors
- the autonomy **quiet-hours** floor correctly raised the effective tier to **2**
- an approval was created (`6888072e-a9ee-4f3c-aeca-e6dccb9a79bd`, pending)
- **no** outbound message row was created
- Meta was correctly **not** called — zero Graph API requests
- all services (worker, API, webhook ingress, cloudflared, Postgres, Redis) remained healthy

Ratna's timezone is `Asia/Kolkata` with a quiet window of **21:00–08:00**. The run landed at
20:22:33 UTC = **01:52 IST**, inside that window, so parking was correct rather than a defect.

**The physical autonomous send must be repeated outside the 21:00–08:00 Asia/Kolkata quiet window.**

**Correction on the record:** the "gold pendant" message was **not** present in `webhook_events` —
a search for `%pendant%` returned zero rows, and the newest actual event still carried
`"Hi, can someone help me?"`. No pendant test occurred; nothing in this repository should be read as
claiming one did. Whether that message was never sent or never delivered by Meta is unresolved and
is the one fact in this trace that cannot be established from the machine.

---

# PILOT-1D-L — PHYSICAL PROOF RECORD (2026-08-17)

**The loop is closed.** A real customer message from a real handset was answered autonomously by
Priya, sent through Meta, and received on the handset. Confirmed visually by the founder and
independently by Meta's own delivery receipts.

## The proven chain

```
real handset
  → Meta
  → Cloudflare tunnel
  → webhook ingress (webhook-only, 404 on everything else)
  → webhook_events (Postgres)
  → automatic WhatsApp normalizer (worker-owned, no manual call)
  → msg.received.v1 → outbox → Redis
  → worker → planner
  → Priya → DeepSeek V4 Flash (thinking disabled)
  → communication_mode = reactive → effective Tier 1 → NO approval
  → mediation → messages.send
  → Meta Graph API → HTTP 200 + wamid
  → real handset receipt CONFIRMED
```

## Evidence

| Stage | Value |
|---|---|
| Inbound webhook | `fabc6e5b-3c86-4f25-acf6-7ab69403e155`, received 01:48:11.336 UTC |
| Normalized | `processed_at` 01:48:12.256 — 0.9s later, automatic |
| Event | `msg.received.v1` `a254f4de-6ec7-4a34-87c4-ad9aa1d0be7f`, published + consumed |
| Run | `c63ca8d5-9ece-487b-85d3-c12de539f297` — **succeeded**, 4 steps, 3.1s, `error = None` |
| Trigger | `msg.received` |
| communication_mode | **reactive** (derived from the trigger, not from any model output) |
| Quiet hours | **ACTIVE** — 01:48 UTC = 07:18 IST, inside Ratna's 21:00–08:00 Asia/Kolkata window |
| Effective tier | **1** — `reply_standard` fired; the quiet-hours floor contributed nothing |
| Approval | **none created** |
| Model | deepseek/deepseek-v4-flash, 1388ms, 594/22 tokens |
| Reply | "VAYLORN PROOF 002 — Yes, I'm here. How can I help you today?" |
| Outbound row | `status = sent`, wamid stored |
| Meta | `POST /<version>/<phone_number_id>/messages` → **HTTP 200 OK** |
| Handset | **CONFIRMED BY FOUNDER** |

The same run evaluated as `proactive` returns tier 2 — so this is a direct, same-store, same-window
demonstration that the reactive/proactive distinction is what made autonomous delivery possible.

## Additional evidence — Meta delivery receipts

Meta subsequently reported the full lifecycle for **both** successful sends:

```
wamid …NzA1MTMA   sent → delivered → read
wamid …RDBFQ0MA   sent → delivered → read
```

`read` is machine confirmation that the message was opened on the handset, corroborating the
founder's visual confirmation. A second message at 01:50 also completed the whole chain, so the
result is reproducible rather than a single lucky run.

## What this closes

- **#37** — a real message has physically reached a phone. Closed.
- **#47** — normalization ran automatically; no manual `normalize_pending()` call.
- **#48** — reasoning explicitly disabled, deadlines correct, 1388ms against a 20s budget.
- **Reactive quiet-hours semantics** — proven in the only way that counts: live, inside the window.

## What it does NOT close

**#43** remains open — see its entry. The corruption incident is contained; shared-test-database
isolation is not solved. Also still open: **#45** (a run reports `succeeded` even when the external
action failed — now with live evidence from PROOF 001's 401), **#46**, **#49**, **#50**, **#51**,
**#52**, and **#53** below.

## #53 — Delivery status never reaches the message row (RESOLVED 2026-08-17 — PHYSICALLY PROVEN)

**Closed 2026-08-17.** Fixed in `62bf7ec` and proven by a real Meta webhook, not by a manual update.

### Physical proof

Message "VAYLORN PROOF 003" produced outbound row `4d77fc11-7519-4123-822f-8282955a5f9f`
(wamid `…YzMDAyRkMA`), sent to Meta with **HTTP 200**. Meta's `delivered` webhook
`abdc8057-68b4-4cf2-8b6d-9078c9bc2308` then arrived and the row moved **`sent` → `delivered`** on its
own: the webhook carried `metadata.phone_number_id`, that number resolved through `channels` to the
owning org, `set_org_context` ran before the mutation, and the UPDATE matched. **No manual UPDATE was
issued at any point** — the row was deliberately left untouched so the corrected path had to prove
itself.

### Edge cases exercised by live traffic, not only by tests

Four status webhooks arrived for that one wamid, out of order and with a duplicate:

```
21:47:13.061  sent
21:47:13.597  read        <- arrived BEFORE delivered
21:47:13.622  delivered
21:47:14.103  delivered   <- duplicate
```

The row ended `delivered`. So production confirmed three properties directly:

- **`read` remains intentionally unrecorded.** It arrived, was processed, and changed nothing — it
  neither blocked the later `delivered` nor moved the row backwards. Whether a customer opened a
  message is still not collected.
- **A duplicate `delivered` is idempotent** — absorbed by `status <> :st`, so no rewrite and no
  second lifecycle effect.
- **Out-of-order statuses did not corrupt state** — a late `sent` on an already-`sent` row was inert.

A second post-restart send (`be6957dc`, 21:47:49) also reached `delivered`, so the result is
reproducible rather than a single observation. One row (`e5bb4855`, 21:45:05) remains `sent`: it was
sent sixty seconds *before* the worker restarted onto the fix, so its `delivered` webhook hit the old
code. It is the last artifact of the defect and a clean before/after boundary.

### Not proven here

**The recovery-attempt linkage remains test-proven only.** `recovery_attempts.mark_delivered` is
covered by the integration tests, but the outbound message in this proof was an ordinary
conversation reply with no linked attempt, and nothing was fabricated to create one. That linkage
will be physically exercised during PILOT-1C ghost recovery.

### Original finding


**Found while recording the proof, pre-existing, NOT introduced by the PILOT-1D-L branch.**

Meta sent `delivered` and `read` webhooks; the normalizer stored and processed them
(`processed_at` set); and `messages.status` is still `sent` for both proven messages.

Cause, from the code: `core/channels/whatsapp/normalizer.py::_apply_statuses` runs

```sql
UPDATE messages SET status = :st WHERE provider_message_id = :pm AND status <> :st RETURNING org_id
```

**before** any `set_org_context`, and `messages` has `FORCE ROW LEVEL SECURITY`. With no
`app.org_id` set the row is invisible, the UPDATE matches zero rows, `org_id` comes back `None`, and
the loop `continue`s — so the status is never applied and `recovery_attempts.mark_delivered` is
never reached either. The tenant-safety reasoning in that docstring is sound; the ordering defeats
it.

Consequences: `delivered`/`read` are invisible to the owner UI and to ROI reporting, and ghost
recovery cannot observe a delivery it depends on. Not a merge blocker for PILOT-1D-L — the send path
itself is proven — but it should be fixed before delivery data is trusted for anything.
