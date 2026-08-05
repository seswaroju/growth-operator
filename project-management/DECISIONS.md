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
