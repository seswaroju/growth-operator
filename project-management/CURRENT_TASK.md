# Current Task

This file always describes exactly one active ticket. When a ticket completes, append its verified summary to
`IMPLEMENTATION_LOG.md` and mark this task as
`Completed — awaiting founder review`.

Do not replace this file with a new ticket until the founder explicitly
selects and approves the next ticket.

---

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
