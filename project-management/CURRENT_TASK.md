# Current Task

This file always describes exactly one active ticket. When a ticket completes, append its verified summary to
`IMPLEMENTATION_LOG.md` and mark this task as
`Completed — awaiting founder review`.

Do not replace this file with a new ticket until the founder explicitly
selects and approves the next ticket.

---

## MVP-034 · Gated send adapter — **Completed — awaiting founder review** (2026-07-30)

Branch `feature/mvp-034-036-send-adapter` (off main). `core/channels/whatsapp/send.py`: the single outbound exit, with four fail-closed gates before any Meta call — (1) **audit capability** (fresh <10min entry authorising this exact `msg.send` → `approval_required`), (2) **execution token** (stub, non-empty required; real one-time binding lands MVP-066 → `approval_required`), (3) **suppression** (marketing/all scope → `suppressed_contact`; lookup error fails closed), (4) **consent** (marketing needs positive consent; transactional exempt → `consent_missing`). On success: outbound `messages` row + `msg.sent.v1` + audit `msg.send:succeeded`; on exhausted failure: `status=failed` + `msg.failed.v1` + `msg.send:failed`. Retries honour 429 Retry-After and retry 5xx ×3 (bounded). Meta stays gated-simulated. **No migration** (schema already had `contacts.consent_status`, `suppressions`, `messages.audit_id`). **191 pytest, 0 skipped.**

**MVP-036 enforcement folded in here** (the suppression+consent join is the same gate).

## MVP-035 · WhatsApp templates management — **Completed — awaiting founder review** (2026-07-31)

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
