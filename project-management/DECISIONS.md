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
