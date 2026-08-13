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
