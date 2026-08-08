# Founder Decisions

Records technical/product decisions the founder has explicitly approved. Append new entries; do not edit past entries (add a superseding entry if a decision changes, and reference the old one).

---

### 2026-07-10 — Implementation repo is a separate directory from the doc vault

**Decision:** Product code lives in `/Users/srila/AI-Growth-Operator/growth-operator/`, a directory separate from `/Users/srila/AI-Growth-Operator/Growth-Operator-Vault/` (the Obsidian doc vault). The code repo's `docs/` is a symlink to the vault, read-only, never modified by implementation work.

**Context:** Initial scaffold work was about to happen inside/alongside the vault; founder explicitly asked for a separate directory for "project implementation code etc." Keeps the vault as pure source-of-truth documentation and the code repo as a clean, independently-versioned artifact.

**Decided by:** founder

---

### 2026-07-10 — New GitHub repo, not an existing one

**Decision:** Push the implementation repo to a newly created GitHub repository rather than an existing remote.

**Context:** No remote existed yet. Founder chose "create a new GitHub repo" over "push to an existing repo URL" when asked.

**Decided by:** founder

---

### 2026-07-10 — Repo name and visibility: `growth-operator`, private

**Decision:** The GitHub repository is named `growth-operator` and is **private**.

**Context:** Matches the local directory name; private visibility chosen as the default-safe option for an unreleased product, over a public alternative.

**Decided by:** founder

**Result:** Repo created and pushed at `https://github.com/seswaroju/growth-operator`, initial commit `cf7536e`.

---

### 2026-07-22 — WABA number: use Srila's existing WhatsApp number

**Decision:** Meta WhatsApp Business API verification will use Srila's existing WhatsApp number (not a new number for Priya).

**Context:** Blocker #3 — longest lead-time item in the MVP plan; blocks MVP-031..037 and the Week-1 exit demo. Porting a new number freezes it for days, so the existing number avoids that delay. Founder chose the existing number when asked.

**Decided by:** founder

---

### 2026-07-22 — Identity tables (migration 001) are global; `users` has no `tenant_id`

**Decision:** Migration 001 (`users, sessions, otp_challenges`) creates **global** identity tables. `users` has **no** `tenant_id` column and no FK to `organizations`; no RLS is applied to any of the three tables. Org membership is modeled separately by `user_orgs` in migration 002 (MVP-014).

**Context:** Conflict between authoritative docs — v1 `docs/06-database/schema.sql` defines `users.tenant_id NOT NULL REFERENCES organizations(id)` (single-org users), while `docs/25-implementation-starter-kit/09-database-migration-order.md` orders identity (001) before orgs (002) and introduces a `user_orgs` many-to-many table, and `CURRENT_TASK.md` notes users/sessions are global. A `NOT NULL` FK to `organizations` cannot exist in 001 (that table is created in 002), and `user_orgs` supersedes a single-org `users.tenant_id`. Resolved in favor of global users + `user_orgs` membership, which is the more implementation-proximate (migration-order) design and lets 001 run standalone. The v1 `users.tenant_id` column is superseded.

**Decided by:** founder

---

### 2026-07-22 — Dependency: add `argon2-cffi` for OTP/token hashing

**Decision:** Add `argon2-cffi>=23.1` to `pyproject.toml` dependencies. Used to hash OTP codes (`otp_challenges.code_hash`) and session tokens (`sessions.token_hash`) at rest.

**Context:** MVP-011 and `docs/25-implementation-starter-kit/13-auth-rbac-approval-audit.md` explicitly specify argon2 hashing. Only `python-jose[cryptography]` (JWT signing) was present; no password-hashing library existed. Per CLAUDE.md §9 the dependency is required by the approved ticket. Purpose: memory-hard hashing of low-entropy secrets at rest. No license/operational concern (MIT/LGPL, pure-Python wheel with C extension).

**Decided by:** founder

---

### 2026-07-22 — Interim OTP channel is email (phone kept behind a flag); defer Meta

**Decision:** While Meta WABA verification is deferred (too long a testing lead-time), the OTP channel switches to **email** for the interim. Email **replaces** phone as the login identifier for now; the phone-OTP code path is **retained behind a config flag** (`GROWTH_OPERATOR_OTP_CHANNEL`, default `email`), not deleted. The broader "replace Meta with a third-party API" direction applies to the WhatsApp **conversation channel** (MVP-031+, not yet in scope) and is tracked for later; real Meta integration is planned once verification completes.

**Context:** This deviates from `docs/25-implementation-starter-kit/13-auth-rbac-approval-audit.md`, which specifies Phone OTP. Rationale: unblock end-to-end OTP testing without waiting on Meta. Implementation: `otp_challenges` generalized to `(channel, identifier)`; `users.phone` made nullable with a CHECK that phone or email is present; migration 001 edited in place (unapplied/uncommitted). Real email/SMS sending remains a gated external side effect (§10.4) — dev echo covers local testing; a real provider adapter + credentials + approval are needed before staging sends. All add-backs tracked in [TODO.md](TODO.md).

**Decided by:** founder

---

### 2026-07-22 — Clarification: Meta WABA is pending API access, not deferred

**Decision/clarification:** Supersedes the "defer Meta" framing in the 2026-07-22 interim-OTP entry above. Meta WhatsApp is **on the critical path and actively wanted** — it is blocked only on Meta granting API access (verification in flight), not postponed by choice. Email OTP is a **bridge** so owner login works while access is pending, not a permanent replacement. When access lands: build the Meta adapter (MVP-031..037), point OTP delivery at WhatsApp, and restore phone OTP.

**Context:** Founder corrected the wording — "Meta WABA is not deferred but need to wait till I get the API access." Records (TODO.md #1, config.py, status.*) updated to "pending API access / bridge" language.

**Decided by:** founder

---

## Open — not yet decided (tracked here for visibility, do not treat as approved)

These appear in `IMPLEMENTATION_AUDIT.md` under "Questions requiring founder decisions" and are **not yet resolved**. Move to a dated entry above once the founder actually decides:

- IBJA gold-rate source: scrape vs. paid API vs. manual-first
- Owner approval channel: WhatsApp-only vs. + PWA push
- Razorpay account entity: personal vs. new company
- Data residency: Hetzner EU vs. India VPS
- React 18 (per spec) vs. React 19 (as scaffolded)
- Judge model choice for eval harness (cost vs. calibration)

---

### 2026-07-29 — RLS membership bootstrap: `app.user_id` self-policy on `user_orgs`; refresh/login re-derive `org_id`

**Decision:** `user_orgs` (migration 002, MVP-014) gets the standard `apply_rls` org policy **plus** a permissive self-policy `p_self` — `FOR SELECT USING (user_id = current_setting('app.user_id', true)::uuid)`. Requests set **two** transaction-local GUCs: `app.user_id` (always, from the JWT `sub`) and `app.org_id` (when known). Consequently, `core/tenancy/tokens.refresh_session` and the OTP verify path **re-derive the user's `org_id` + role from `user_orgs`** (the source of truth) and embed them in every freshly-minted access token; the tenant middleware (MVP-016) will set both GUCs for all requests/jobs.

**Context:** `user_orgs` RLS keys on `org_id` and fail-closes with no context, but `/me`, org-create idempotency, and — critically — **refresh** must read a user's membership *before* any org context exists (a bare refresh token carries only `sub`/`sid`, so without this the 15-minute access-token refresh would silently drop `org_id` and break every downstream tenant query). The self-policy lets a user read only their **own** membership rows (isolation still holds: a user never sees another user's memberships), which is the standard multi-tenant bootstrap pattern. Alternative considered: denormalize `org_id`/role onto `sessions` (rejected — denormalized, role-sync burden, and migration 001 defined sessions as pure identity). Founder chose the self-policy from a 2-option prompt.

**Decided by:** founder

---

### 2026-07-29 — Ratified: `apply_rls` hardened with `NULLIF(current_setting(...), '')`

**Decision:** `migrations/lib/rls.py` policies use `NULLIF(current_setting('app.org_id', true), '')::uuid` (and migration 002's self-policy the `app.user_id` equivalent). This normalises both the unset (NULL) and the pooled-connection empty-string cases to NULL before the `::uuid` cast, so a missing tenant context fails **closed to zero rows** rather than raising `''::uuid` (a 500) on a PgBouncer transaction-mode connection that previously ran `SET LOCAL app.org_id`.

**Context:** The literal SQL in `docs/21-platform/multi-tenant-rls.md` (`current_setting(...)::uuid`) assumes unset returns NULL, which is only true on a connection that never set the GUC; under transaction pooling the reset value is the empty string. The hardening preserves the doc's stated "no context = no rows" intent while fixing the edge. Founder ratified from a 3-option prompt ("Ratify + keep going"). App-level RLS **enforcement** still requires a non-BYPASSRLS `app_rw` role (BLOCKERS #11, MVP-016) — this decision is about policy correctness, not enforcement.

**Decided by:** founder

---

### 2026-07-29 — Batch sequencing: MVP-017/018/019 now; MVP-020 deferred behind 024/025

**Decision:** Implement MVP-017 (staff invite), MVP-018 (api_keys, migration 004), MVP-019 (messaging, migration 005) as the next batch, then commit + push to main. **MVP-020 (packs, migration 008) is deferred** until after MVP-024 (audit, migration 006) and MVP-025 (events, migration 007), because a linear alembic chain requires 006/007 in front of 008 — the same constraint that motivated pulling migration 010 forward (2026-07-29). Reaching 020 now would mean front-loading the two heaviest event/audit tickets; instead 020 runs right after 024/025 in a later batch.

**Context:** Founder chose "Defer 020; do 017–019 now" from a 3-option prompt. Keeps migrations strictly linear with no re-parenting.

**Decided by:** founder

---

### 2026-07-29 — Add an `invites` migration not listed in the authoritative migration order

**Decision:** MVP-017 needs an `invites` table (global, expiring, no RLS) that does **not** appear in `docs/25-implementation-starter-kit/09-database-migration-order.md`. Approved to add it as a new migration appended after messaging (005) in the alembic chain, plus the owner-only invite + accept-on-OTP-login endpoints, flag-gated behind `invites.enabled=false`.

**Context:** The authoritative migration-order doc omits invites entirely; per CLAUDE.md §15.2 adding/reordering a migration needs founder approval. Founder chose "Add it." The `docs/` vault is read-only, so this repo-side decision record is the authority for the deviation until the vault is updated.

**Decided by:** founder

---

### 2026-07-30 — webhook_events is global (not org-scoped RLS) in migration 005

**Decision:** In migration 005 (messaging, MVP-019), six tables — channels, contacts, conversations, messages, message_templates, suppressions — are org-scoped with RLS. **`webhook_events` is global (no `org_id`, no RLS)**, with a `UNIQUE (provider, external_id)` for idempotent ingress. `messages` gains a denormalized `org_id` (v1 scoped it only via `conversation_id`) so the standard org RLS policy applies.

**Context:** MVP-019 / the migration-order doc list `webhook_events` among "7 tables, RLS on all". But raw webhook ingress arrives **before** the tenant is known (a webhook is matched to a channel→org during processing), so an org-scoped INSERT would fail the RLS check (no context / NULL org_id). Making it global is the architecturally-correct reading of its own "raw immutable ingress" role. Deviation from "RLS on all 7" flagged for founder review; schema-only with no data yet, so trivially revisitable. Also: `messages.audit_id` and `conversations.assigned_agent` are plain uuids (no FK) until their referenced tables exist (audit_log/006, agents/packs).

**Decided by:** Claude (flagged for founder ratification) — architectural necessity, not a preference.

---

### 2026-07-30 — event_outbox is global (not org-scoped RLS), like webhook_events

**Decision:** `event_outbox` (migration 007, MVP-025) is a global table (has `org_id` but no RLS). It's written inside org transactions via `emit()` but drained by a single cross-org publisher, which — running as the non-BYPASSRLS `app_rw` role — could not read other orgs' rows under RLS. Same rationale and precedent as `webhook_events` (2026-07-30). Not exposed to request handlers; only `emit()` (writer) and the publisher (reader) touch it.

**Decided by:** Claude (flagged for founder awareness) — pipeline table serving a cross-org system publisher.

---

### 2026-07-30 — Seed 5 archetypes (not 6): `support` has no level-1 allowlist

**Decision / flag:** Migration 008 (MVP-020) seeds **five** agent archetypes — concierge, nurture, campaigner, ops, planner — with `capability_allowlist` matching `docs/implementation/agents/tool-permissions.yaml` byte-for-byte (the ticket's binding acceptance criterion + "level-1 truth"). The ticket / schema comment mention a **sixth** archetype (`support`, which has a jewelry prompt), but **no level-1 allowlist is defined for it** in tool-permissions.yaml or anywhere else, so it cannot be seeded byte-for-byte and is omitted.

**Founder action needed:** to seed `support`, define its level-1 `capability_allowlist` in the vault's tool-permissions.yaml; then it can be added to `core/packs/archetypes.py` + migration (or a follow-up migration). Until then, 5 archetypes stand.

**Decided by:** Claude (flagged for founder resolution) — doc inconsistency (6-vs-5).

---

### 2026-07-30 — CRM money stored as integer minor units (`*_minor bigint`)

**Decision:** Migration 011 (CRM, MVP-023) stores money as integer minor units — `orders.total_minor`, `attributions.amount_minor` (bigint) — rather than v1's `numeric(12,2)`. Matches the platform money model (topics.yaml `total_minor:int`, the float-money guard's intent) and keeps amounts consistent with the event payloads that carry them. Also: `attributions.agent_id` is a plain uuid (no FK) until an agents table exists.

**Decided by:** Claude (flagged) — aligns with the platform's stated money convention.

---

### 2026-07-30 — Consumer/scheduler/events implementation choices (MVP-026..030)

**Decisions (flagged):**
- **MVP-028 scheduler uses a hand-rolled cron matcher, not `croniter`** — CLAUDE.md §9 discourages adding a dependency for a small amount of straightforward code. Supports `*`, lists, ranges, and `*/n` steps in the five fields; timezone-aware via stdlib `zoneinfo`. The `jobs_runs` observability table is **deferred** (run start/end/status go to structured logs; not needed for the acceptance criteria — lock proof + tz firing).
- **MVP-029 backoff** = the idle-reclaim interval (5 min) between redeliveries, a pragmatic stand-in for per-message exponential backoff; DLQ after 5 retries (6th failure) with error history + `alert.ops` emit; `scripts/dlq-replay.py` re-injects.
- **MVP-030** uses the ticket's allowed "generated specs + checksum" variant: `scripts/gen_events.py` writes `core/events/types.py` (payload specs + checksum) from topics.yaml; a drift test fails CI if stale; `emit()` validates payloads at runtime. Static pydantic models (compile-time typing) are a later refinement.

**Decided by:** Claude (flagged for founder awareness) — all satisfy the tickets' acceptance criteria.

---

### 2026-07-30 — WhatsApp channel built gated/simulated; `resolve_channel` helper migration

**Decisions:**
- **MVP-031..037 WhatsApp adapters are built in gated/simulated mode.** Ingress signature-verify + normalize are fully real and tested; real Meta *sends* and webhook *registration* stay behind a flag + founder approval (§10.4, BLOCKERS #3, TODO #1). No real external calls in code or tests.
- **`resolve_channel(type, external_id)` SECURITY DEFINER function** added (small migration appended after 011, not in the migration-order doc — same pattern as `invites`/`resolve_api_key`). The message normalizer (MVP-033) must resolve org-from-webhook before any tenant context exists, but `channels` is RLS-scoped; the function does that one RLS-exempt exact lookup.

**Decided by:** Claude (flagged for founder awareness) — architectural necessity + the approved gated-adapter approach.

---

### 2026-07-30 — STOP/UNSUB auto-suppress replies with a transactional confirmation (MVP-036)

**Decision (founder-approved):** When a customer sends a STOP/UNSUB keyword, the inbound normalizer auto-suppresses the contact (scope=`marketing`) **and auto-sends a one-line transactional confirmation** ("You've been unsubscribed…") via the gated send adapter — without human approval. This is an automated outbound send, which §19 (human-in-the-loop for customer replies) otherwise gates; the founder explicitly approved this narrow exception because it is a legally-expected compliance acknowledgement, transactional-class (exempt from marketing consent), and self-limiting (one message per STOP).

Bounds on the exception: it fires **only** on a matched STOP/UNSUB keyword, sends **only** the fixed platform confirmation text (no model-generated content), is transactional-class, and remains gated-simulated until `whatsapp_live_enabled`. The send still passes the MVP-034 gates (it mints its own audit capability + execution token), so it is fully audited. No other automated customer-facing send is authorised by this decision.

**Decided by:** Founder (2026-07-30), in response to Claude flagging the §19 conflict before implementing.

---

### 2026-07-31 — MVP-035 full scope: template status webhooks + channels.waba_id (touches MVP-031)

**Decision (founder-approved):** Build MVP-035 at **full scope**, including webhook-driven template status updates. Meta's `message_template_status_update` webhook is keyed by WABA id, but the WABA id lived only inside the encrypted channel credential — so to resolve the org, migration `83efabba79ee` adds a queryable `channels.waba_id` column (an account identifier, not a secret; the access token stays encrypted) plus a `resolve_channel_by_waba` SECURITY DEFINER lookup, and MVP-031's already-merged `connect.py` now populates `waba_id` on connect. Modifying merged connect.py is justified: it's additive, backward-compatible (existing rows get NULL and simply won't resolve status webhooks until reconnected), and the alternative (parsing the encrypted blob for every status webhook) is worse.

Also: the `send()` adapter gained an optional `template=(key, language)` path (template gate + `send_template`); freeform sends are unchanged. Real Meta submission/webhooks remain gated (#3).

**Decided by:** Founder (2026-07-31), selecting "Full scope incl. waba_id + connect touch."

---

### 2026-07-31 — MVP-038 pack contracts model the pack DATA where it deviates from core-platform.md signatures

**Decision (flagged, §4):** The `core/packs/contracts.py` models follow docs/21-platform/core-platform.md, but the authoritative pack **data** under `verticals/` deviates from those illustrative signatures in several places. Since the acceptance criterion is "every verticals/* file parses," the data wins and the models match it, with the deviations recorded here:
- `AgentBinding.kpi_defs` (spec) → `kpis: list[str]` + `budgets: dict` (data); bindings.yaml also carries a top-level `planner:` block → `BindingsPack.planner`.
- `PricingStrategyDef.rule_schema`/`rate_source_requirements` (spec) → `rules`/`rate_sources` (data); modelled `_Open` because rules are engine-specific (validated by the pricing engine, MVP-050+).
- `CatalogSchema.identity_keys` is a **list of composite key-lists** (`[["huid"],["sku"]]`), not `list[str]`; the on-disk file is a JSON-Schema-plus document, split via `CatalogSchema.from_document` (version from the `$id` tail).
- `IntegrationSpec.mcp_server` may be a bare string; integration files carry provider-specific extras (calendars, sources_ref, usage, templates_namespace) → modelled `_Open`.
- Auxiliary files not in the core-platform.md signatures (onboarding/ui/calendar/evals) got models too (founder chose full scope 2026-07-31) so literally every contract file parses.

Models are **strict** (`extra="forbid"`) where the platform owns the shape (manifest, bindings core, catalog, workflow, calendar) and **open** where the pack/engine does. Prompt `.md` anchor-splitting is MVP-039; the `templates/` seed is MVP-035 — both excluded from the 038 contract walk.

**Decided by:** Claude (flagged for founder awareness) — required to satisfy "every file parses"; the core-platform.md signatures are illustrative, the pack files are ground truth.

---

### 2026-07-31 — MVP-040 installer: status machine (active/failed) + deferred steps + failed-status migration

**Decisions:**
- **Install status machine uses the schema's allowed set** (`pack_installations.status` CHECK from migration 008): `installing → active` on success (not the spec's "installed" — the table has no such value), `→ failed` on rollback, `→ uninstalled` on uninstall. Idempotency matches an existing **active** install of the same bundle digest.
- **Migration `5dcbda42efca`** adds `'failed'` to the status CHECK (additive — widens the allowed set only). The installer needs to distinguish a rolled-back attempt from one still installing. NOT a migration-order change; a new head migration for 040's own status machine.
- **Steps 4 (policies) + 5 (workflows) are deferred no-ops** — their tables (`approval_policies`/014, `workflow_definitions`/016) belong to MVP-065/072, not built (founder chose "build installer, defer policies/workflows", 2026-07-31). BLOCKERS #14.
- **A binding for an unseeded archetype is skipped** (logged) — `support` was omitted from the archetype seed (MVP-020), so jewelry installs 4 bindings/instances (concierge/nurture/campaigner/ops), kirana 3.
- **Digest stored in `pack_installations.config._digest`** (no dedicated column) as the idempotency key.

**Decided by:** Founder (deferral, 2026-07-31) + Claude (flagged: status-value mapping, the additive failed-status migration, unseeded-archetype skip).

---

### 2026-07-31 — Dependency approvals: zstandard (pack transport) + clamav/MinIO (media)

**Decision (founder-approved):** Add the dependencies deferred as BLOCKERS #12/#13 (§9):
- **`zstandard`** (pack bundle `.tar.zst` transport, MVP-039 follow-up) — done 2026-07-31; resolves #13.
- **ClamAV (antivirus) + MinIO/S3 (object store)** for real WhatsApp media handling (MVP-037 follow-up) — resolves #12; wires the real `MediaScanner`/`MediaStore` behind the existing `media_av_enabled`/`media_storage_enabled` flags.

**Decided by:** Founder (2026-07-31), explicitly choosing "add them now" for both.

---

### 2026-07-31 — MVP-045 catalog: history-table shape, idempotency, pack resolution, dep direction

**Decisions (flagged):**
- **`catalog_items_history` extends the doc's `LIKE catalog_items INCLUDING ALL`** — used `INCLUDING DEFAULTS` (not ALL, which would copy the `id` PK — invalid for a multi-version history) plus history metadata: `history_id` (PK), `operation`, `changed_by` (actor), `reason`, `changed_at`. Required so history rows carry the actor + reason the acceptance mandates (§4: doc schema was a starting point).
- **`catalog_idempotency (org_id, idempotency_key → item_id)`** backs the POST `Idempotency-Key` header (same key → the same item; no new column on catalog_items).
- **Pack resolution:** an item's `pack_id` + `attributes_schema_ver` come from the org's single **active** `pack_installation` (by priority) and the latest `catalog_schemas` row — items don't carry pack_id in the API (matches openapi CatalogItem).
- **Identity dedup is app-level** (SELECT-then-check against the pack's flattened `identity_keys`, e.g. huid/sku) rather than a DB unique constraint — a rare same-identity race could slip a duplicate; a partial unique index can harden it later.
- **Dependency direction:** MVP-045's ticket lists "Dependencies: MVP-042", but 042 (index gen) is *on* catalog_items, so 045 must come first. 045 validates against `catalog_schemas` (registered by the installer, MVP-040). **045 now unblocks 042.** Deep attribute validation (JSON Schema + CEL) is MVP-046.

**Decided by:** Claude (flagged for founder awareness) — all satisfy the ticket's acceptance; the schema-doc deviations are recorded above.

---

### 2026-07-31 — MVP-046 attribute validation: additionalProperties + jsonschema dep

**Decisions (flagged):**
- **`jsonschema` promoted to an explicit main dependency** (it was already present transitively; now declared so production has it). CEL is `cel-python` (already a dep).
- **The validator injects `additionalProperties: false`** at the top level — the pack schemas don't set it, but the acceptance requires unknown attributes to be rejected. Platform policy: an attribute not in the pack schema is an error.
- **JSON Schema (Draft 2020-12) runs first; CEL constraints only if the structure is valid** — a malformed shape would make cross-field CEL misfire. Problems come back as `{path, error, rule}` (rule="schema" for structural, the CEL expr for constraint failures).
- Compiled validators + CEL programs are **cached per (pack, version)** for the <10ms budget.

**Decided by:** Claude (flagged) — satisfies the ticket acceptance; jsonschema was already installed.

---

### 2026-07-31 — MVP-048 embeddings built gated-simulated; provider deferred (founder choice)

**Decision (founder-approved):** Build the full semantic-search pipeline now with a **deterministic simulated embedder** (seeded PRNG per text — no paid API), gated behind `embeddings_provider_enabled` (off → simulated; on → the real provider, not yet wired → fails closed). The real hosted embedding provider is the founder's pick later (§9), same pattern as WhatsApp/media. Design points: kNN via pgvector `<=>` (cosine, HNSW); **RRF k=60** fuses BM25 + kNN; a kNN neighbour joins `results` only within `SEMANTIC_MAX_DISTANCE` (0.35) else it's offered as `nearest`; the empty→nearest contract returns the 3 closest when there are no confident results. BLOCKERS #16.

**Decided by:** Founder (2026-07-31, "Build 048 gated-simulated").

---

### 2026-08-02 — MVP-050 pricing engine: safe AST evaluator, exactness, and the pg-014 golden flag

**Decisions (flagged):**
- **The engine evaluates formulas with a safe AST interpreter, not `eval`** — a whitelist of node types (arithmetic, compare, call, attribute, subscript, ternary, list-comp), no imports/builtins. Pack formulas are trusted (installed + signed), but the whitelist is defence-in-depth. A small preprocessor rewrites the pack DSL to Python before parsing: `&&`/`||`/`!` → `and`/`or`/`not`, `x[].f` → a comprehension, `map(seq,v,e)` → `[e for v in seq]`, `c ? a : b` → `(a) if (c) else (b)`.
- **Exactness (the money invariant):** every value is an int (minor units) or `Decimal` — **floats are rejected** (`config_schema_violation`). Each money stage must resolve to an integer minor value; a non-integer **residue fails closed** (`unledgered_figure`). `rate()` pins its snapshot id into provenance; a stale rate raises `stale_rate`. `compute()` is pure → replayable byte-for-byte.
- **The repo golden files are illustrative samples** (jewelry 5, kirana 4 — the "200/60 total" full suites aren't in the repo). The engine is verified against the self-consistent samples + engine-property tests.
- **pg-014 sample golden is inconsistent with the authoritative formula.** The strategy.yaml discount formula caps at 5% of the **full subtotal** (incl. making) → 258768; the sample golden expects 239600 (5% of metal only). The engine follows the **formula** (the executable spec); the sample looks hand-written and wrong. Flagged for founder — if the intended rule is "discount excludes making," the *strategy.yaml formula* should change (`stage.subtotal - stage.making`), not the engine.

**Decided by:** Claude (flagged) — the formula is authoritative per §4; pg-014 is a suspect fixture, not a rule to invent (§29).

---

### 2026-08-02 — MVP-055 executor: LangGraph adopted; runtime migration lands ahead of approvals-014

**Decisions (founder-approved, 2026-08-02):**
- **LangGraph is the agent-executor orchestration engine.** `langgraph>=0.2,<0.3` added as a main dependency (resolved `langgraph==0.2.76`, MIT). It sequences the `route→compose→model_turn→tool_call→respond` graph and provides checkpoint/resume. **Footprint disclosed at approval:** it pulls in `langchain-core==0.3.86`, `langgraph-checkpoint==2.1.2`, `langsmith`, `orjson`, `tenacity`, and ~12 more transitive packages (17 total) — the largest dependency added to date. **Boundary:** LangGraph only *sequences*; the platform gates (mediation/approval/committed-figures ledger/audit) remain the authority, and every node fails closed the same as the rest of the system. The custom-orchestrator alternative was offered and declined.
- **The LLM stays gated-simulated** (deterministic, provider-agnostic `core/runtime/model.py`); the real provider is chosen at go-live (prior decision, 2026-08-02). No paid API in tests.
- **Migration ordering:** the runtime migration (`agent_runs`/`agent_steps`/`agent_memory`/`model_routes`) chains off `63bcec3ea528` (013) **now**, ahead of the approvals migration (014, MVP-065) which is not yet built. Safe because no runtime table FKs into the approvals tables; approvals will slot in later with no FK conflict. This deviates from the doc's 014-then-015 file order — founder approved landing runtime first.

**Decided by:** Founder (2026-08-02, "Yes, please do both of the recommended actions… I approve").

---

### 2026-08-03 — MVP-068 approvals: notification-state migration (ticket said "none")

**Decision (flagged):** MVP-068's ticket states "Database changes: None — approvals table (014) carries notification state columns already." It does **not** — neither the split migration 014 (MVP-065) nor the `approvals` object (MVP-067, built from `docs/06-database/schema.sql`, which also lacks them) defined notification-state columns. The escalation ladder needs to track its progress, so a small **additive** migration (`bb65660f0771`) adds `notified_at`, `reminded_at`, `escalated_at`, `notify_ref`, `notify_channel` to `approvals`. RLS was already on the table; round-tripped. The founder pre-approved "MVP-068 and further" with this migration flagged in the check-in.

**Decided by:** Claude (flagged) — the ticket's "carries these already" premise was incorrect against the authoritative schema; the columns are required for the ladder. Founder pre-approved the ticket with the migration disclosed.

---

### 2026-08-03 — MVP-070 trust ledger: `approvals.trust_settled` marker (ticket said "trust_ledger rows")

**Decision (flagged):** the hourly settle job must add +1 to `clean_approvals` **at most once** per tier-2 approval, no matter how often it runs. The robust way is a per-approval settled marker, so migration `30b7edf76a9d` adds `approvals.trust_settled boolean NOT NULL DEFAULT false` (+ a partial index on the unsettled tier-2 set). The ticket lists DB changes as "trust_ledger rows" only; this is a small additive deviation. The alternative (a per-run watermark on `trust_ledger.updated_at`) is more fragile (a missed run window is harder to reason about) and the per-approval "no incident in the 72h window" check is cleaner with the marker. This is the third small approvals-column addition across the approvals cluster (068 notification state, 070 settle marker) — all additive, all flagged.

**Decided by:** Claude (flagged) — required for idempotent settlement; founder pre-approved MVP-070 with the migration disclosed in the check-in.

---

### 2026-08-04 — MVP-063 failure contract: `incidents` table lands early; `org_id` naming; provider-exception → `provider_unavailable`

**Decisions (flagged, founder pre-approved the runtime-hardening cluster incl. MVP-061 "and further"):**
- **The `incidents` table lands now (`da3474bd3cdb`), ahead of its scheduled slot.** The migration-order doc schedules `incidents` under migration 018 (campaigns/metrics cluster, MVP-074), but the circuit breaker (MVP-063, a P0 runtime-safety ticket) needs it now to record tier-2 failures and circuit trips. It lands as the next revision — **additive, no FK conflict** (nothing earlier references it). Migration 018 (MVP-074) must **skip** re-creating it. Same pattern as the MVP-055 runtime-ahead-of-approvals deviation.
- **The table uses `org_id` (not the authoritative `schema.sql` `tenant_id`).** Every org-scoped table in this repo uses `org_id` + `apply_rls(table)` (which keys on `app.org_id`); matching that convention keeps RLS uniform. The shape otherwise follows `schema.sql` (severity/title/status/opened_at/closed_at) **extended** with the runtime linkage the breaker records: `run_id`→`agent_runs`, `instance_id`→`agent_instances`, `kind`, `action_type`, `detail`. `circuit_open` is **already** an allowed `agent_instances.status` value (008 CHECK) — no status migration.
- **A tool implementation that raises is converted to a structured `provider_unavailable` `ToolResult`** at the proxy's execute step (was: the exception propagated out of the run). This is the failure contract — a flaky provider becomes a recoverable, breaker-countable failure instead of crashing the executor. Only infrastructure failures (`provider_unavailable`) trip the breaker; policy denials (manifest/param/rate/budget) do not. No new error code (`provider_unavailable` is canonical).

**Decided by:** Claude (flagged) — required for the breaker to record + count failures; founder pre-approved the runtime-hardening cluster ("approve continuing into the runtime-hardening cluster… Later we can move on to worker/scheduler wiring and approvals schema reconciliation later").

---

### 2026-08-04 — MVP-064 model routes + failover: `costs_lite` lands early; realistic provider names, gated-simulated (Option A)

**Decisions (founder-approved posture — "move on with option A", 2026-08-04):**
- **Provider posture = gated-simulated (Option A).** The failover chain runs over the deterministic simulated provider: `get_provider(name)` resolves **every** provider name to `SimulatedProvider` until `llm_provider_enabled`, at which point real clients (registered in `_REAL_PROVIDERS`) take over and fail closed until wired — the same gate as `RealModel`/embeddings. So routing + failover + cost logging are fully built and tested with **no vendor, no network, no spend**; the real-vendor swap at go-live changes nothing in `routing.py`. The real-provider alternative (wire a vendor now) was explained and declined.
- **Seed uses realistic provider names.** `model_routes` is seeded with `anthropic`/`openai` primary/fallback pairs (not placeholder names) so the go-live swap is a registry change only. Names are inert today — they resolve to the simulated client.
- **`costs_lite` lands now (`3680972ace7a`), ahead of the migration-order doc.** The doc doesn't enumerate a cost table; MVP-064 needs one for per-route/run cost attribution, so it lands as the next revision — org-scoped, **+RLS**, additive, no FK conflict (flagged; same pattern as `incidents`). The `model_routes` seed ships in the same migration (idempotent `ON CONFLICT (node_key) DO NOTHING`) so staging/prod and CI get the routes automatically.
- **Cost is a placeholder estimate.** `cost_usd` is computed from a static per-1k-token price map (anthropic/openai) until real pricing at go-live; tokens are real (from the model turn). The point is route/run **attribution**, not billing accuracy.

**Decided by:** Founder (2026-08-04, "I agree lets move on with option A") + Claude (flagged the `costs_lite` early-landing, same additive pattern as prior runtime tables).

---

### 2026-08-04 — Approvals-cluster schema reconciliation (doc → code; canonical schema recorded)

**Context:** the approvals cluster shipped across MVP-065/067/068/070 with several **flagged,
additive** deviations from the authoritative vault `docs/06-database/schema.sql` (068 notify columns,
070 `trust_settled`, plus the `org_id` naming). This entry **consolidates** those into one canonical
record and resolves the standing "approvals-schema reconciliation" thread. Full audit + canonical
DDL + the drafted vault patch: [approvals-schema.md](approvals-schema.md).

**Findings:** the database is correct and tested (518 pytest; `test_approval_*`, `test_batch_rls`).
The vault `schema.sql` is **stale** — it defines only a v1 `approvals` (with `tenant_id`,
`requested_by NOT NULL REFERENCES agents(id)`, `approver`) and **none** of the four policy-engine
tables (`approval_policies`, `trust_ledger`, `incident_tightening`, `execution_token_jti`). The
shipped `approvals` uses `org_id` (repo-wide convention, `apply_rls` keys on `app.org_id`),
`requested_by` nullable → `agent_instances` (no `agents` table exists), `approver_user_id`, and 11
feature columns absent from the vault (run_id, edited_payload, matched_rules, reason_code, audit_id,
notified_at, reminded_at, escalated_at, notify_ref, notify_channel, trust_settled).

**Decision (founder-approved, 2026-08-04 — "Doc→code + repo-side reference"):** align the *record*
to the *code*, not the reverse. The tested DB is canonical. The code-→-doc alternative was rejected
as destructive (renaming `org_id`→`tenant_id` breaks `apply_rls` + all 22 migrations; dropping the
11 columns breaks shipped 067/068/069/070 features; the `agents` FK targets a nonexistent table).
**Actions:** (1) `project-management/approvals-schema.md` is the interim authoritative reference;
(2) the founder applies the drafted patch to the read-only vault `schema.sql` (replace the
`approvals` block + add the four tables); (3) **no code or migration change** — nothing is broken.
The `agents` vs `agent_instances` divergence in the vault is noted as a broader agent-model
reconciliation, out of scope here.

**Decided by:** Founder (2026-08-04, "Doc→code + repo-side reference"). Supersedes the piecemeal
approvals-migration flags (2026-08-03 × 2) for the schema-of-record question.

---

### 2026-08-04 — MVP-056 planner routing: taxonomy loaded from the pack; gated-simulated keyword classifier

**Decisions (founder-approved 2026-08-04):**
- **The intent taxonomy is loaded from the pack bundle, not `agent_bindings`.** The ticket says "taxonomy loaded from agent_bindings," but the installer never persists `tasks[].intents` to that table (it carries only tool grants / tiers / kpis). Rather than add a migration (out of ticket scope), the planner loads the taxonomy through the pack-layer interface `core/packs/taxonomy.py` — reading the pack's declarative `agents/bindings.yaml` by path and caching per pack slug. `core/` never imports `verticals/` (Rule Zero, §11.3); it reads declarative config, the sanctioned pattern.
- **Classification is gated-simulated (Option A posture, extended).** The "classify via small route" step is a deterministic **keyword matcher** over pack-provided `intent_keywords` (longest-match-wins), added to `verticals/jewelry/agents/bindings.yaml` (pack authoring — declarative, editable, not the vault). A small classifier model can replace it at go-live behind the same seam, exactly like the LLM/embedder/providers. This keeps jewelry classification knowledge in the pack and makes the routing_golden deterministic (20/20).
- **`tenant_paused` = `organizations.status != 'active'`.** No dedicated flag exists; the org status is the tenant-level pause signal (the planner drops a paused tenant's traffic).
- **Guard classes:** concierge/support/ops route as `transactional` (in-conversation replies, exempt from the frequency cap); nurture/campaigner route as `marketing` (the cap applies). So an inbound reply always flows; the cap bounds planner-initiated marketing touches (AC).

**Decided by:** Founder (2026-08-04 — selected MVP-056 + the "pack keyword map" classifier). Note: the `support` archetype is referenced by the pack but not seeded in `agent_archetypes` (concierge/nurture/campaigner/ops/planner are) — support routing resolves correctly but finds no instance until seeded; a pre-existing gap, out of scope here.

---

### 2026-08-05 — MVP-044 pack seeding: policies seeded; prompt-layers already worked; tool→action bridge deferred

**Decisions (founder-approved 2026-08-04 — "MVP-044 scoped to prompt-layers + approval-policies seeding; workflow_definitions later"):**
- **Correction: prompt-layer seeding was already implemented** (`_seed_prompt_layers`, MVP-040) and the jewelry prompt files parse into layers (4 concierge + 1 nurture + 1 campaigner + 2 ops + 1 support = 9, status `candidate`). The "0 rows" was simply that no jewelry pack was installed in the inspection DB. So the real MVP-044 work was **`_seed_policies`** (a deferred stub) — now implemented.
- **`_seed_policies` seeds `approval_policies` (scope='pack') from each binding's `tier_defaults`**, `action_type = applies_to` **verbatim** (the AC is fidelity to the pack — seed matches the pack rules, diff = ∅). Domain fields map: `30m`→`timeout_s=1800`; `on_timeout: hold_and_remind`→`hold` (the DB CHECK allows hold/safe_default/cancel; the remind is the ladder's job, MVP-068); `approver: role:owner`→`approver_chain=['role:owner']`; `condition`→`cel_expr`. Idempotent per (pack, action, description). `workflow_definitions` seeding stays deferred (016/MVP-072 table not built) — founder-approved.
- **New RLS migration `b6456b200baa` (`p_pack_ins`).** `approval_policies` (014) forced RLS with a tenant-only write path, so the installer (app_rw, in the tenant transaction) couldn't insert a global `scope='pack'` row. Added an INSERT policy permitting **only** `org_id IS NULL AND scope='pack'` — mirrors `prompt_layers`' `p_layers_ins` but **tighter**: `scope='core'` (platform tier-4 minimums) stays migration/owner-only, so no tenant path can forge a core rule (tested). Tenant-row isolation unchanged.
- **Flagged follow-up — the tool→action bridge.** The pack tier rules key on abstract actions (`action.message.send`, `action.quote.send`), but the mediation proxy queries the policy engine by **tool name** (`messages.send`). So the seeded pack policies are faithful data-of-record but **do not yet fire** on tool calls; a tool→action mapping (proxy/engine) is a follow-up (BLOCKERS #20). Until then drafts stay safe — an un-matched tier-eval action fails safe to tier-2 (approval).

**Decided by:** Founder (2026-08-04 scope approval) + Claude (flagged: prompt-layers-already-worked correction, the RLS migration, and the tool→action bridge gap).

---

### 2026-08-05 — Tool→action bridge (BLOCKERS #20): tool calls resolve to an abstract-action family

**Decisions (founder-approved 2026-08-05 — "proceed with #20", taxonomy confirmed):**
- **A tool call is evaluated against the abstract action(s) it governs, max tier wins.** `engine.resolve_actions(tool, params)` maps `messages.send`→`action.message.send`, `campaigns.execute`→`action.campaign.execute`, `catalog.write`→`action.catalog.write` (else the tool name). `engine.evaluate_tool(...)` pools the matching contributors across the whole family and applies the "no rule → tier-2" fallback **once**; the proxy's tier check calls it. This is what makes the seeded MVP-044 pack tiers actually fire.
- **"A message with a price is a quote."** `messages.send` also counts as `action.quote.send` when it carries a price — a structured `amount_minor`, or (fallback) the largest money figure parsed from the body with MVP-054's `extract_amounts`. `amount_minor` is populated from that price so the `amount_minor >= 10000000` (₹1,00,000) CEL evaluates; below-threshold no-discount quotes fall back to the plain-reply tier (1).
- **Optional-attribute conditions are `has()`-guarded in the pack.** `discount_any` and `escalation_triggers` referenced `attributes.discount_minor`/`sentiment`/`topic`, which are absent on a normal reply — the engine's fail-safe (`_matches` → True on a CEL error) then made them always-match. Guarding with `has(attributes.x) && …` (a jewelry-pack edit) makes an absent field mean "condition not met", while the engine's fail-safe semantics for genuinely broken rules are unchanged. Pack authors must `has()`-guard optional fields (footgun noted).
- **Known limitation:** a discount expressed only in free text (no structured `discount_minor`) isn't caught (a small discounted quote could slip to tier-1). The agent should pass structured `amount_minor`/`discount_minor` on a quote; body-figure parsing covers the amount but not the discount. Acceptable for the MVP.

**Decided by:** Founder (2026-08-05, "proceed with #20" + confirmed "a message with a ledgered price = a quote").

---

### 2026-08-05 — Autonomy model: owner-adjustable "volume knob" within a fixed platform floor

**Decision (founder-approved 2026-08-05):** the store owner controls a free **autonomy "volume knob"** — they may tighten *or* loosen how much the agent handles autonomously for **customer-facing / operational** actions (replies, quotes, campaigns), dialing it up or down at will based on their comfort. This **supersedes** the current *tighten-only + trust-gated loosening* model for the tenant autonomy keys (`core/tenancy/settings.py` `_is_looser` / `SettingLoosenError`): the owner will be able to loosen freely, not only after earning trust.

**Non-negotiable floor (kept fixed at any knob position):** money-moving / irreversible / public actions **always keep the owner in the loop** — `payment.charge`, `payment.refund`, `payout.create`, `supplier.order_commit`, `ads.publish`, `gbp.update` (the engine's `CORE_TIER4_ACTIONS`, tier 4). No knob setting can lower these. The founder explicitly confirmed refunds/money actions must always require the owner.

**Scope / when:** this is the model for the **autonomy-settings UI** (≈ MVP-088), not built here. The trust ledger (MVP-070) becomes *advisory* (it can suggest loosening) rather than a gate. When implemented: relax the tenant-key tighten-only rule to free-dial, keep the `CORE_TIER4_ACTIONS` floor absolute, and surface the knob per capability. No code change today — recorded so the autonomy UI is built to this intent.

**Decided by:** Founder (2026-08-05, "refunds, money actions the owner has to be kept in loop" + agreeing to the adjustable knob within that floor).

---

### 2026-08-05 — Executor→composer wiring: the prompt activation pipeline (grounded drafts)

**Context (founder-approved 2026-08-05 — "do the full pipeline"):** the composer (MVP-059) existed but nothing produced the `prompt_bindings` it renders from (0 rows), base layers were never seeded, and the executor still used the MVP-055 skeleton prompt. The founder approved building the **full activation pipeline** (not a thin wiring) so a routed run composes a real grounded prompt.

**Decisions / build:**
- **Base layers are platform-seeded, idempotently, from `prompts/base/<archetype>.md`** (`core/prompts/base_layers.py::ensure_base_layer`; global `org_id NULL`, task `'*'`). An archetype with no base file returns None → activation skips it (skeleton fallback). **Only `concierge` has a base file today**, so only the concierge (the customer-facing grounded-draft agent) is activated; nurture/campaigner/ops/support fall back to the skeleton until their base layers are authored.
- **Install-time activation** — a new installer step `_activate_prompts` (after `bindings_instances`): for each concierge (instance, task) it seeds the base layer, `generate_tenant_layer` from settings, finds the pack's vertical layer by the binding's `prompt_layer.ref` anchor, and `pin_binding`s them. The binding task (`catalog_answer`) maps to the vertical anchor (`catalog`) via the pack ref. Compat mismatch or missing vertical → skip that task (never fails the install).
- **Executor uses the composer** — `Deps.compose` (a `(state)→(text, hash)` callable); the executor injects `_make_compose(org, instance, persona)` which resolves the active binding for the run's task and `render`s it, **falling back to the skeleton** when there's no binding or composition errors (composition never blocks a run). `agent_runs.composed_prompt_hash` now reflects the grounded prompt.
- **Base version aligned 1.0 → 1.4** in `prompts/base/concierge.md` to match the vertical's `Composes on base.concierge >= 1.4`. *(Observed but not fixed here: `registry._satisfies` doesn't strip the space in `">= 1.4"`, so the `>=` compat check is currently lenient — a latent parse quirk; the version alignment is correct regardless.)*

**Decided by:** Founder (2026-08-05, "do the full pipeline… this is one important thing to do"). Discovery flagged: the activation orchestration + base-layer seeding were entirely unbuilt (not just the executor call).

---

### 2026-08-05 — #2 close the send loop: the reply is a gated `messages.send`; `send()` reuses the caller's session

**Decisions (founder-approved 2026-08-05 — "total approval"):**
- **A concierge reply is sent through the proxy's `messages.send`, not a separate respond effect.** The executor, at the `respond` node, routes the reply text through `deps.execute_tool("messages.send", …)` — so a **plain reply auto-sends (tier 1)** and a **reply carrying a price parks for approval (tier 2)** and sends on approve (`_park_send` + `resume_after_approval`). This keeps every customer-facing send on the one tier-gated, audited, figure-checked path (MVP-054). `message_class="transactional"` for a concierge reply.
- **`_messages_send` runs the real send inside the proxy's transaction.** It mints the send authorization (audit capability + single-use execution token) and calls `send()` — all using the **passed proxy session**. A gate refusal returns a structured `{"sent": False, "refused": …}` (never crashes the run / trips the breaker).
- **`send()` gained an optional `session` param (MVP-054 refactor).** `org_scoped_session` takes a **per-org advisory xact lock**, so a tool impl inside the proxy (which already holds that lock) cannot open a second `org_scoped_session` for the same org — it deadlocks (statement timeout). With a passed session, `send()` runs its gates + queued row + outcome in the **caller's single transaction** instead of its two-phase commit; standalone callers (the normalizer) still get the self-committing two-phase behaviour. Trade-off: the passed-session path loses "queued row durable before the external call" — acceptable while Meta is simulated (no real external effect to lose); revisit at go-live.
- **On reject**, only the customer-safe close (`SAFE_CLOSE_TEXT`) is sent (tier 1); the original priced reply never goes out.

**Also recorded for #4 (imports, next):** the catalog import must accept **API + CSV + Excel** uploads. The ticket track already covers API (MVP-076) + CSV (MVP-078); **Excel (.xlsx)** is an added scope requirement (founder, 2026-08-05) to fold into MVP-078's extraction.

**Decided by:** Founder (2026-08-05, "total approval to commit" + "support for both API, CSV or excel").

---

### 2026-08-05 — MVP-076 imports: `python-multipart` dependency; import tables land at 017

**Decisions (founder-approved 2026-08-05):**
- **Added `python-multipart` (>=0.0.32, Apache-2.0).** FastAPI's file-upload handling (`UploadFile`/`Form`/`File`) requires it — without it the app can't import. It is the de-facto FastAPI companion for `multipart/form-data`; the only alternative (hand-parsing multipart) isn't worth it. Needed for `POST /v1/imports` (the core of the imports track). Pure-Python, no runtime services.
- **`import_batches` + `import_rows` land at migration 017** (per the migration-order doc) — org-scoped, +RLS. Not in the vault `schema.sql` (like `incidents`/`costs_lite`) — flagged; the vault should add them when the founder next reconciles.
- **MVP-076 scope = the foundation:** migration + `POST /v1/imports` (caps enforced: ≤500MB / ≤200 images / ≤5k CSV rows → RFC-7807 problem + chunking hint) + the resumable **state machine** (`state.py`, legal-only transitions) + the **SSE relay** of `import.batch_state`. Extraction (077/078, incl. **Excel** via openpyxl), review (079), and load/revert (080) are deferred per the ticket split. Blob storage is an in-process seam (real object storage at go-live, like media). xlsx row-cap is enforced at extraction (078), not upload.

**Decided by:** Founder (2026-08-05, "I approve installing python-multipart").

---

### 2026-08-05 — Growth Operator control plane: the cross-tenant platform-admin path

**Context:** the founder wants a **Growth Operator dashboard** — a real, local-first (cloud later) operator console to track store owners and handle support tickets — **distinct** from the store-owner console. The first slice (support tickets: owner-raises → operator queue → resolve, with priority/severity) forces the platform's first **cross-tenant** read/write, which cuts against the strict per-org RLS everything else fails-closed on. Founder approved the approach and scope "exactly as mentioned."

**Decisions (founder-approved 2026-08-05):**
- **`platform_admins` allowlist is the SOLE authority for cross-tenant (operator) access** — a table of user ids, granted via `scripts/grant_platform_admin.py` / `make grant-admin`. It is **deliberately NOT** the org-scoped `founder` role (which grants ALL_PERMISSIONS including `platform:admin`): letting a per-store role confer cross-tenant reach would be a tenant-isolation escalation. `get_admin_db` verifies the allowlist, then sets the **transaction-local** `app.platform_admin='on'` GUC (== `SET LOCAL`, like `app.org_id`; never session-level). No flag → strictly org-scoped, so absence fails closed.
- **RLS is split by command, not `FOR ALL`.** `support_tickets` has `p_read`/`p_update` carrying the admin exception (`org_id = app.org_id OR app.platform_admin='on'`) but `p_insert` is **org-only**. Rationale: a single permissive `FOR ALL` policy's USING becomes INSERT's implicit `WITH CHECK`, so the admin flag would let an operator **file into another tenant** — an isolation test caught exactly this. The operator can read + resolve across tenants, but never insert into one. This split is the pattern for future cross-tenant admin tables.
- **New module + tables land outside the vault** — `core/support/` (a new L0 platform module, no industry nouns), `core/tenancy/platform_admin.py`, and migration **018** (`support_tickets`, `platform_admins`) are not in the vault `schema.sql`, migration-order doc, or core module map (same posture as `incidents`/`import_batches`) — flagged for the next vault reconciliation.
- **The `support.ticket.raised.v1` outbox event is deferred**, not dropped: adding it to `core/events/topics.py` would break the topics-drift test against the read-only vault `topics.yaml`, which must add it first. The operator queue reads by poll, so the loop doesn't need it; it lands when the vault registers the type (for operator notifications).
- **Explicitly out of scope for now** (founder): billing/MRR and SSO (billing is deferred platform-wide per CLAUDE.md §11.2); the tenant roster/health views and the rest of the control plane are later slices; wiring the owner console's message-understanding to a **real Anthropic model** ("understand any message") is a separate approved track — the model stays **simulated** for now (founder chose "later").

**Also recorded (roadmap):** two product surfaces are planned and are to be **ported to a real domain with auth** later — the **store-owner console** (owner logs in → their dashboard) and the **Growth Operator console** (founder logs in → cross-tenant operator dashboard). Implementation deferred; noted so it isn't lost.

**Decided by:** Founder (2026-08-05: "I want one for Growth-operator's dashboard … keep track of them, jira tickets kind of thing"; "both built-in + owner-raised … priority and severity"; "works perfectly even if its local … used later when we deploy on cloud"; "I want exactly as you mentioned so you can get started").

---

### 2026-08-06 — Enterprise hardening of the cross-tenant operator plane

**Context:** the founder asked for "industry/enterprise (Google/Apple-level)" security on the
cross-tenant admin capability — "a major issue if every store owner sees every other customer" —
and to knock the items out one at a time with rigorous corner-case tests.

**Guarantee (unchanged, now locked):** store owners remain **strictly org-isolated** (RLS,
fail-closed, tested). The `app.platform_admin` flag is the *only* cross-tenant read path and it
opens **exactly one table — `support_tickets` — and nothing else** (not conversations, contacts,
catalog, revenue). Owners can never reach the admin plane (403/404) and can never self-grant.

**Built now (this branch):**
1. **Least-privilege lock (security #1).** An exhaustive structural test asserts the
   `app.platform_admin` exception appears in exactly one table's RLS policies and never in any
   INSERT `WITH CHECK`; a runtime test proves the flag is inert on other tenant tables. Adding the
   exception anywhere else fails CI (teeth-verified against an injected regression).
2. **Immutable admin-plane audit (security #2).** A dedicated append-only `platform_access_log`
   (migration 019, immutability trigger like `audit_log`) records **every** cross-tenant action —
   reads (queue views: who/when/count/filters) as well as writes — separate from the per-tenant
   audit chains. Tamper-evident (UPDATE/DELETE blocked for all roles).
3. **Allowlist governance (security #3).** `platform_admins.expires_at` (migration 020) →
   `is_platform_admin` treats an expired admin as not-an-admin (fail closed); `grant`/`revoke`
   scripts (`make grant-admin … [--days N]` / `make revoke-admin`) set expiry and write the
   grant/revoke to the access log.
4. **Admin plane off by default (security #4).** `admin_plane_enabled` (default **false**) — every
   `/v1/admin/*` endpoint returns **404** (existence hidden, before auth) unless explicitly enabled.
   Prod stays off unless deliberately turned on; readies a separate deployment.

**Required before the operator plane is exposed on cloud (deploy-time, NOT yet built):**
- **MFA / step-up auth** for operators (WebAuthn/TOTP), beyond the OTP primary — a compromised
  operator credential must not be enough for cross-tenant reach.
- **Separate deployment + network isolation** — run the admin plane as its own service behind a
  VPN / IP-allowlist, not reachable from the tenant app; `admin_plane_enabled` stays false on the
  tenant deployment.
- **Dual-control (four-eyes) for allowlist grants** — a grant requires a second operator's approval
  (today grants are a local bootstrap script).
- **Anomaly + rate alerting** on `platform_access_log` (bulk/after-hours cross-tenant reads →
  alert) and **logging of denied** admin attempts (403/404) for probing detection.
- **PII minimization** in the operator view — ticket bodies are owner-written and may contain PII;
  scope/redact what the operator sees.

**Also (dev convenience, gated):** `otp_dev_fixed_code` — a fixed local-dev OTP (e.g. `000000`) so
the founder can sign in locally without a delivery adapter. Same guardrails as the dev echo: None by
default, honoured only when `env == 'dev'`, startup **fails closed** outside dev or on a malformed
code, and the code is never persisted / returned / logged. Set `GROWTH_OPERATOR_OTP_DEV_FIXED_CODE`
in the local env only.

**Decided by:** Founder (2026-08-06: "suggest industry/enterprise level security … has to be top
notch (google/apple level)"; "make the OTP as fixed … 000000"; "make this as TODO list and lets
knock out one at a time").

---

### 2026-08-06 — Two-plane RBAC (Phase 1): tenant `owner/manager/staff/viewer` + platform `dev/admin/staff/analyst`; retire `founder`

**Context:** Phase 1 of the multi-plane program. Founder chose (2026-08-06): separate apps, **full role matrix now**, ROI-now (marketing/competitive as vision), and asked the design to follow "bigger enterprise" practice.

**Decisions (founder-approved 2026-08-06):**
- **Two separate authorization planes** — the AWS/GCP/Stripe control-plane vs data-plane split. Tenant RBAC (`core/tenancy/permissions.py`) and a SEPARATE platform RBAC (`core/tenancy/platform_permissions.py`). The permission namespaces are disjoint (`resource:action` vs `platform.resource:action`) and a **plane-separation test** enforces it — no tenant role can ever confer a platform permission or vice-versa.
- **Retire the tenant `founder` role + `platform:admin` permission.** A tenant role granting a platform permission blurred the plane boundary and was a latent cross-tenant escalation: anyone who set a membership to `founder` held `platform:admin`, which any `@requires(PLATFORM_ADMIN)` endpoint would honor. No flow creates `founder`, so retiring it is free and closes the footgun. Cross-tenant power now lives ONLY in the `platform_admins` allowlist + platform roles.
- **Tenant roles `owner/manager/staff/viewer`**, ranked; **invites carry a role** and you can't grant above your own rank. New permissions (conversations/customers/campaigns:read/insights/members:manage/billing) are defined now but deny-by-default until their feature ships (Phase 3+).
- **Platform roles `dev/admin/staff/analyst`** on `platform_admins.role` (migration 022; existing operators default `admin`), enforced by `require_platform(perm)` — the platform analogue of tenant `requires(perm)`.
- **Two mislabeled endpoints re-homed:** `api_keys` (issue key for own org) + `ops` (own-org run viewer) were gated on the tenant `platform:admin`; both act on the caller's own org → re-gated to `org:manage` (tenant).
- **Constant-based enforcement stays** (correct for our stage). Growth path recorded but NOT built: custom roles, then a policy engine (**Cedar / OpenFGA / OPA**) once sharing/hierarchy complexity demands it.

**Decided by:** Founder (2026-08-06: "full matrix now"; "how would bigger enterprises design it … I would lean towards that [retire]"; "extend invites to carry a role, I don't want to defer"; "start doing in that order").

---

### 2026-08-06 — Phase 2: two separate front-end apps (customer + operator), vitest, `/v1/admin/me`

**Context:** Phase 2 splits the single `web/` app so store owners and Growth Operator staff have separate logins + dashboards (founder: "I want 2 apps. Separate login information, separate dashboard etc for customer and for people in growth operator").

**Decisions (founder-approved 2026-08-06):**
- **Two separate Vite apps**, not one multi-build: `web/` (customer, port 5173) and `web-ops/` (operator, port 5174). They share the FastAPI backend but **no front-end code** — the operator bundle (cross-tenant reads) never ships to a store's browser. This realises the Phase-1 "separate deployment + network isolation" decision at the app layer. A little api-client duplication is the accepted cost.
- **`vitest` added** (dev-only dependency, both apps) so the front-end role-gating/auth logic is unit-tested, not just type-checked — the "test rigorously" bar applies to the UI too.
- **New `GET /v1/admin/me`** — the operator app's front-door "who am I + what can I do" (role + platform permissions), mirroring the tenant `GET /v1/me`. Nav/visibility is driven by the permissions the endpoint returns (backend is the source of truth → no drift).
- **`admin_plane_enabled` gate moved** to `core/tenancy/platform_admin.py` (shared by both `/v1/admin/*` routers). The operator app is off-by-default: when the plane is disabled the app shows a clear "operator console is turned off" state.
- **Front-end gating is UX only** — every action is still enforced server-side (RBAC + platform allowlist). `make make-owner` is a local dev stand-in for the not-yet-built store-onboarding flow.

**Decided by:** Founder (2026-08-06: "I want 2 apps. Separate login information, separate dashboard"; "Got it on vitest, agreed and we need that"; "[/v1/admin/me] Also agreed"; "proceed to ticket 2.2/2.3").

---

### 2026-08-06 — Analytics & Intelligence layer: CEO-grade math lives in the OPERATOR console (Phase 4); store owner gets layered outcomes + an ask-GO channel

**Context:** planning Phase 3 (customer dashboards), the founder rejected deferring campaigns + ROI and instead expanded scope to a **CEO-grade analytics + intelligence layer** — "like the high-end Google Analytics or some other Shopify analytics" — so that as the operator ("as a CEO") they have a **mathematical understanding of why a campaign is or isn't working**, plus a **competitor-analysis agent** report. Then refined *where* each audience sees it. This entry records the vision + the plane split so it is **not lost** (founder: "did you add … math, google analytics … to Phase 4 … we should not forget previous conversation").

**Decisions (founder-approved 2026-08-06):**
- **A real analytics/intelligence ENGINE is built (not vanity metrics), once, and scoped by plane.** Layers: (1) **event facts** materialized from the existing `event_outbox` CloudEvents + `leads`/`quotes`/`contacts`/`messages`; (2) **rollups/metrics** via the scheduler; (3) **attribution** (touch → conversion → attributed revenue); (4) **campaign analytics** — full funnel (audience→delivered→replied→lead→quote→visit→sale), conversion rates, **statistical significance** (is +X% real or noise?), and **drop-off diagnosis** = the "*why*"; (5) **ROI/business metrics**; (6) an **agent-insight framework** where agents write structured reports.
- **The heavy math + intelligence live in the GROWTH OPERATOR / CEO console — Phase 4.** The full funnel, significance tests, attribution model, ROI math, and the **competitor-analysis + marketing-strategist agent reports (with their reasoning)** render in `web-ops` (operator plane). This is the analytics-heavy surface.
- **The store-owner dashboard shows only the distilled OUTCOME by default** — plain language, e.g. "Diwali campaign → +12% inquiries, 2 sales, ₹1.8L attributed; **it worked** because festival timing landed; next: repeat for Akshaya Tritiya." No funnel/significance/competitor machinery renders on the owner side by default.
- **Layered transparency (a dimmer, not a curtain).** Insight/outcome records carry graded content — `verdict → drivers → full_breakdown → evidence`. A **knowledgeable owner can drill down on demand** (verdict → drivers → full breakdown), as deep as they want. The owner controls how much they see.
- **An owner⇄GO Q&A thread per insight — trust because money is involved.** The owner can **ask** a question about a specific result ("how is ₹1.8L attributed to *this* campaign and not walk-ins?"), and **Growth Operator answers into the owner's dashboard** (agent-drafted or human-written). Reuses the messaging/thread pattern (like `support_tickets`) scoped to an insight instead of a ticket.
- **Competitor data model = owner-tracked competitors + LLM research.** The store names the competitors to watch (`tracked_competitors`); the competitor-analysis agent researches/synthesizes a report. **Real web/LLM output is wired later.**
- **LLM stays gated-simulated now; wired later.** Build the analytics data models + agent-report framework now (the durable value) against the simulated model; wire real Anthropic (competitor + marketing agents) as a gated, cost-controlled, founder-approved step later — same posture as WhatsApp/embeddings/providers. Meaningful statistics require real pilot traffic; early dashboards are correct but sparse (expected, not a bug).

**Roadmap of record (supersedes the earlier "Phase 5 = ROI/insight framework" framing):**
- **Phase 3 — Customer dashboard** (`web`): operational sections on real data — 3.1 Home+shell · 3.2 Approvals · 3.3 Conversations/leads · 3.4 Catalog · 3.5-sec CRM · 3.6-sec Settings+autonomy. Owner-appropriate outcomes only.
- **Phase 3.5 — Analytics & Intelligence foundation** (shared engine, feeds both planes): A1 event-facts + rollup pipeline · A2 campaigns model + funnel/significance ("why") · A3 attribution + ROI/business metrics · A4 agent-report framework + `tracked_competitors` + competitor-analysis & marketing-strategist agents (simulated) + the layered insight record & owner⇄GO thread.
- **Phase 4 — Operator / CEO console** (`web-ops`): cross-store roster + health + **the full analytics/intelligence per store** (the math, the funnel, the significance, attribution, ROI, and every agent's report + reasoning). *This is where the Google-Analytics-grade views live.*
- **Phase 5 — folded into 3.5/4** (ROI/attribution/insight framework is no longer a separate late phase).

**Decided by:** Founder (2026-08-06: "build data models like the high-end google analytics … mathematical model, why the campaign is or isn't working … competitor analysis agent"; "these discussed items mainly for growth operator dashboard, only the final summary or solutions to store owner's dashboard"; "if store owner is knowledgable … ask for more details … I should be able to respond from growth operator to store owner's dashboard … build trust as there is money involved"; "did you add … math, google analytics … to Phase 4 … we should not forget").

---

### 2026-08-07 — Autonomy volume-knob wired live (Option A): free-dial within an immovable tier-4 floor

**Context:** Ticket 3.6 implements the owner-adjustable autonomy "volume knob" the 2026-08-05 entry
scoped. Discovery: the `autonomy.*` settings existed but were **inert** — nothing in the executor/
proxy/engine read them; the real gate is the approval-engine tiers. The founder chose **Option A**
("that way its not fake and genuinely it works") — actually wire the knob into the live decision.

**Decisions (founder-approved 2026-08-07):**
- **The knob is a max-tier overlay on the approval engine.** `engine.evaluate_tool` adds an
  `_autonomy_floor` contributor: `auto` respects the pack/tier rules; anything else — or the global
  `autonomy.paused` — forces approval (`AUTONOMY_REVIEW_TIER = 2`). Because `select_decision` takes
  the **max**, the overlay can only **raise** a tier, never lower one → the `CORE_TIER4_ACTIONS`
  money/irreversible floor (payment.charge/refund, payout.create, supplier.order_commit, gbp.update,
  ads.publish) stays **absolute at every knob position** (proved by test at auto **and** paused).
- **Free-dial supersedes tighten-only** (per 2026-08-05): the `autonomy.*` keys are now
  `tighten_only=False`, so the owner may loosen *or* tighten freely — the old `TightenOnlyViolation`
  on loosening is retired for these keys (the class/mechanism kept for any future tighten-only key).
- **Default = `auto`** for messaging/pricing/campaigns. Rationale: the pack tiers + tier-4 floor
  already park everything risky (quotes/discounts/campaigns/money), so `auto` auto-sends **only
  truly routine replies** — safe *and* good UX. Wiring the knob therefore **changes no existing
  behaviour** until an owner tightens (zero churn to the send-loop/bridge tests). Flipping the
  default to `draft_only` (approve-everything-by-default) remains a small future change if desired.
- **Capabilities:** `messaging` (replies), `pricing` (quotes — a priced reply picks this up),
  `campaigns`. Plus a global **`autonomy.paused`** panic switch (forces approval everywhere).
- **Cheap adds included** (founder asked "add more scope … note the rest"): the **global pause
  switch**; the **settings-change audit trail** was found to **already exist** (`write_setting`
  records `settings.changed` with an old→new diff). Deeper autonomy depth (per-action thresholds,
  context/VIP routing, spend caps, trust-ramp, vacation mode, dry-run, explainability) recorded in
  `project-management/PRODUCTION_DEPTH_BACKLOG.md`.

**Decided by:** Founder (2026-08-07: "I feel option (A) … that way its not fake and genuinely it
works"; "add more scope … maynot be for MVP but note down … production level in notes somewhere").

---

### 2026-08-07 — Analytics & Intelligence engine: migrations 023/024 (additive, flagged); rollup-from-domain-tables; campaign events not yet emitted

**Context:** starting the founder-approved Phase 3.5-eng (analytics/intelligence engine, ~8 tickets
A1→A4.3). This entry records the first two tickets' structural decisions + the honest scaffolding
findings, consistent with the "real, not fake" bar.

**Decisions (founder-approved plan 2026-08-07):**
- **Migrations `023 business_metrics` + `024 campaigns` land additively off head `022`,** not in the
  vault migration-order doc (which schedules a campaigns/metrics cluster under 018/MVP-074). Same
  posture + precedent as `incidents`/`costs_lite`/`support_tickets` (flagged; MVP-074 must skip
  re-creating `campaigns`). Both org-scoped (+RLS forced), up/down round-tripped.
- **A1 computes metrics by rolling up the domain tables, not a separate event-fact store.** The
  scheduled `business_metrics_rollup` (daily, per-org, trailing 30 days) recomputes counts from
  `leads`/`quotes`/`orders`/`messages` + upserts idempotently (UNIQUE + ON CONFLICT). A dedicated
  `analytics_facts` event-log is **not** built — the domain tables carry timestamps, so rollups are
  the leaner real foundation. Deferred to the backlog if event-level detail is later needed.
- **Honest finding (flagged): `campaign.*` events are defined but emitted by nothing.** There is no
  campaign send-lifecycle yet (the campaigner agent's execution is future work). So A2.1 builds the
  `campaigns` model + a **create-record** path (campaigns exist + are measurable now) + the
  **`campaign.executed` consumer wired and ready** (idle until a real send-flow emits). The A2.2
  funnel/significance will therefore be **correct-but-sparse until real campaign traffic exists** —
  expected, not a defect. The campaign send-lifecycle is out of the analytics engine's scope.

**Decided by:** Founder (2026-08-07 phase-plan approval: "Yes, I confirm both. Lets start from
A1->A4"; "Commit the A1+A2 batch to main first").

---

### 2026-08-08 — Campaign analytics engine: exact first-touch attribution + unhackable ROI + layered insight record (A2.2–A4.1)

**Context:** the founder wanted **both** the "why it worked" engine (Option A) **and** exact
attribution from the start (Option B), plus a plain-language reasons layer, all "solid, unhackable,
industry-grade … complex yet easier to understand in terms of depth and clarity."

**Decisions (founder-approved 2026-08-08):**
- **Exact attribution is deterministic single-touch (first-touch), not multi-touch.** A conversion
  is credited to the campaign that FIRST touched the contact within the attribution window (default
  30d) before it — auditable, no estimation (`campaign_touches`, migration 025). **Multi-touch
  credit-splitting is the genuinely-ambiguous case (a modeling choice, not a fact) and is deferred**
  to `PRODUCTION_DEPTH_BACKLOG.md`, per the founder ("record the backlog so we can get to it later").
  A `campaign_metrics` rollup table was intentionally NOT built — analytics compute on-the-fly per
  view (also backlogged).
- **ROI is "unhackable" by construction.** Revenue derives *only* from immutable `orders.total_minor`
  attributed by the deterministic rule — **no field lets anyone inject a revenue figure**. Cost =
  real `sent_count` × the owner's `campaign.cost_per_message_minor` setting (auditable, not a guess);
  cost 0 ⇒ ROI undefined (never a fake infinity). Everything is org-isolated (RLS + explicit filter,
  isolation-tested) and recomputable from source records.
- **The "why" is a one-sample proportion z-test vs the store's baseline** (real lift or noise, 95%),
  + funnel conversion + drop-off (bottleneck stage). Two-sample/control-group + confidence intervals
  + Bayesian small-sample are backlogged.
- **A layered insight record: `verdict → drivers → full_breakdown → evidence`** (migration 026
  `agent_reports`). `drivers` are plain-language reasons with a good/bad/neutral flag ("add other
  reason … with information or note there"). This is the owner's progressive-disclosure record and
  the shape the campaign-analysis producer (A4.2) + simulated agents (A4.4) write into.
- **Migrations 025/026 land additively off 024** (not in the vault; flagged — precedent
  `incidents`/`costs_lite`). No LLM (the A4.4 producers stay gated-simulated).

**Decided by:** Founder (2026-08-08: "I want both … can we achieve that?"; "solid, unhackable,
industry grade level. Complex yet easier to understand in terms of depth and clarity"; "add other
reason (with information or note there?)"; "Let's record the backlog … commit to main CI cleanable").

---

### 2026-08-08 — Intelligence producers (A4.2–A4.4): deterministic campaign analysis + gated-simulated competitor/marketing agents

**Decisions (founder-approved 2026-08-08):**
- **A4.2 — the campaign-analysis producer is deterministic (no LLM).** It runs the A2/A3 engine and
  stores the result as a layered `agent_report` (`campaign_analysis`, `model="deterministic"`). This
  is the numeric analysis — it does not need and does not use a model.
- **A4.3 — `tracked_competitors` (migration 027)** is the owner-curated list of rivals; owner/manager
  (`campaigns:send`) manage it, all roles (`insights:read`) view it. It's the input to A4.4.
- **A4.4 — the competitor-analysis + marketing-strategist agents are gated-simulated.** While
  `llm_provider_enabled` is off (default) they produce **deterministic, clearly-labelled** output
  (`model="simulated"`, verdict carries "Simulated analysis — a real model + live data replace this
  at go-live"); when the flag is on but the real agent isn't wired they **fail closed**
  (`provider_unavailable`) — identical posture to `RealModel`/embeddings/providers. The competitor
  agent's body is a placeholder over the tracked list (real web/LLM research is future); the
  marketing agent's heuristics are grounded in the store's real weekly metrics. Swapping in the real
  LLM + a competitor-data source at go-live changes no interface. No paid API, no network in tests.

**Decided by:** Founder (2026-08-08 phase approval "Yes, lets proceed" through A4.2→A4.4; the
LLM-simulated-now/real-later posture was set 2026-08-06, DECISIONS analytics entry).

---

### 2026-08-08 — A4.5 owner⇄GO thread: the cross-tenant surface widened to a SECOND table (scoped operator INSERT)

**Context:** the owner⇄Growth-Operator Q&A thread on an insight (`insight_messages`, migration 028)
needs the operator to **answer cross-tenant into the owner's dashboard** — a genuinely new capability
vs. support tickets, where the operator only reads/resolves and can **never** insert into a tenant.

**Decisions (founder-approved 2026-08-08, "the sensitive cross-tenant one"):**
- **Split-RLS with a scoped operator INSERT.** `p_read` = own-org OR the `app.platform_admin` flag;
  `p_insert` = `(own-org AND author_type='owner') OR (platform_admin AND author_type='operator')`.
  So an owner posts only owner-authored rows into their own org, and an operator posts only
  **operator-authored** rows (its answer) into any tenant. Append-only (no UPDATE/DELETE). A
  `resolve_report_org` SECURITY DEFINER helper finds a report's org without broadening
  `agent_reports` RLS. Every operator reply is audited to `platform_access_log`.
- **The least-privilege lock (security #1) was updated, not loosened.** The `app.platform_admin`
  blast radius is now **exactly two** deliberately-chosen tables — `support_tickets` + `insight_messages`
  — each with its own isolation tests. The flag may appear in an **INSERT WITH CHECK only on
  `insight_messages`, and only scoped by `author_type='operator'`** (a structural test enforces both;
  a teeth test proves an owner cannot forge an operator-authored message). Any future third table, or
  an unscoped operator INSERT, fails CI.

**Decided by:** Founder (2026-08-08: "the sensitive cross-tenant one" / approving A4.5). The scoped
extension is the implementation of the founder's owner⇄GO answer-into-dashboard requirement
(DECISIONS 2026-08-06 analytics layer).

---

## 2026-08-08 — Vision-intake scoping decisions (post-braindump)

Following the founder braindump captured in `VISION_INTAKE.md` (17 items). None of the items are
built; these decisions govern **how** they will be, when each gets its own ticket.

**Approved (founder, 2026-08-08):**

- **Q4 — Agent knowledge/config home: HYBRID.** Cross-vertical agent knowledge (marketing
  frameworks, SEO rules, competitor tactics) lives in a **shared base** with **per-vertical
  overrides**, loaded by the runtime. It stays **out of `core/`** (rule zero — no industry logic in
  core). Directory layout follows the `project-management/vision/` tree once expanded.

- **Q3 — External-tool policy: self-host on our own cloud, integrate by API, never vendor.** AGPL
  tools (plausible, listmonk) and any external service run as **separate self-hosted containers in
  our own cloud/VPC**, called over our API boundary — never copied into `core/`. This keeps our app
  closed-source-safe (AGPL copyleft only bites on modified, publicly-exposed copies of *their* code)
  and keeps customer data inside our boundary. *Self-hosting is the cloud-native path — it means we
  run the tool on our cloud, not that we avoid cloud.* External-action / privacy-sensitive tools
  (listmonk sends, Instagram/FB/social posting, fingerprintjs) stay **simulated/gated** until
  explicit founder + ToS/privacy sign-off.

- **Q2 — CRM (#3) & HITL (#10): delta-extraction, not fork-and-adopt.** We keep our **RLS-native**
  CRM (`core/customers`) and approval engine (`core/approvals`). We **mine** trycompai/crm and
  block/buzz for the specific solved problems/features we're missing and **port those into our own
  modules** as tickets. Rationale: these repos are whole applications; retrofitting our `app.org_id`
  tenant isolation into external code is more work than porting features AND re-opens the
  cross-tenant risk we guard hardest — and modifying AGPL code we run as a service triggers copyleft.
  Delta-extraction captures their hard-won fixes without the isolation risk or the license
  entanglement. *(Founder delegated: "whichever you recommend.")*

- **Q1 — Build sequence: all four, one ticket at a time.** Founder wants A4.6, security-hardening,
  Phase 4, and the marketing-agent framework layer — all of them. Recommended order (pending
  confirmation per ticket): **(1) A4.6** finish the analytics/intelligence UI (in-flight, small);
  **(2) Security-hardening** — close the audit's real gaps (error tracking + backup/restore drill +
  gitleaks history scan) to protect the first pilot; **(3) Phase 4** operator/CEO console (home of
  the item-17 GO dashboards); **(4) Marketing-agent framework layer** (item 1 — safe, no external
  side-effects). Each still goes through the normal plan → approval → branch → verify → merge cadence.

**Security-audit first-pass (item 16) recorded in `VISION_INTAKE.md`:** strongest areas are cross-
tenant isolation (c) and server-side authz (b); real gaps are **error tracking (d)** and **tested
backups (e)**; payments (f) N/A (no payment code yet). Drives the security-hardening ticket above.

**Decided by:** Founder (2026-08-08 answers to Q1–Q4). Verbatim braindump + full item-by-item
scoping in `project-management/VISION_INTAKE.md`.

---

## 2026-08-08 — Security-hardening tooling (S1 + S2 direction)

Sub-tickets of the security-hardening initiative from the audit in VISION_INTAKE.md (#16). Sequence
S1 → S2 → S3 approved by the founder 2026-08-08.

- **S1 secret scanning: gitleaks, pinned binary, no third-party action.** CI installs a pinned
  gitleaks 8.30.1 binary (not the marketplace action → no supply-chain exposure, no license gate) and
  scans the **full git history** with `--redact`. A `.gitleaks.toml` extends the default ruleset with a
  **tight** allowlist: only the confirmed false positive (`private_key: Ed25519PrivateKey` — a function
  parameter annotation) and SOPS `*.enc.yaml` (encrypted by design). Rationale: proving "no secrets in
  code/history" must be enforced continuously, not assumed; a narrow allowlist keeps the teeth (a
  planted fake token is still caught).

- **S2 error tracking: self-hosted GlitchTip (decided, built later).** Founder (2026-08-08): *"I want
  both the good if not best UX and the working has to be tight."* GlitchTip is the only option that
  delivers **both** — a Sentry-grade dashboard (grouped errors, counts, alerting) **and** error data
  that never leaves our cloud (self-hosted, integrated by API, per the Q3 external-tool policy). Sentry
  SaaS is rejected (payloads can carry customer data off-boundary); OTLP-only is a weaker UX. To be
  implemented in S2 with a frontend error boundary + backend exception capture feeding it.

**Decided by:** Founder (2026-08-08). S3 (backup + tested restore) tooling TBD when S2 completes.

---

## 2026-08-08 — Security-hardening S3: backup + tested restore (audit #16e)

- **The restore DRILL is the deliverable, not just backups.** #16e was "backups never restored"; an
  untested backup is a false sense of safety. `scripts/db_restore_drill.sh` dumps → restores into a
  throwaway scratch DB → verifies (table count + `alembic_version` + `organizations` row count) →
  drops scratch. **It runs in CI (`migrate` job) on every push** (founder-approved 2026-08-08), so the
  restore path is proven continuously — this is what closes #16e. Also `make backup-drill` locally
  (piped into the dev Postgres container → no host pg tools needed). Verified against pg16: 71 tables,
  head `9f9334d2999a`, orgs round-tripped → PASS.
- **Restore guardrails.** `scripts/db_restore.sh` refuses any target named `*prod*` and refuses to
  overwrite the primary DB without `--force`. Dumps are gitignored (`/backups/`, `*.dump`) — they hold
  real data and must never be committed.
- **Production automation deferred** to PRODUCTION_DEPTH_BACKLOG (scheduled + off-site + encrypted
  backups, PITR/WAL, scheduled drill against real backups, RTO/RPO).

**Decided by:** Founder (2026-08-08 — approved "run the drill in CI too").
