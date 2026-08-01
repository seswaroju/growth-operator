# MVP Ticket Status Ledger

One scannable row per ticket, **with the date it was implemented**. This is the quick
index; the sources of truth stay:

- **[IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md)** — full dated detail per completed ticket (append-only).
- **[CURRENT_TASK.md](CURRENT_TASK.md)** — the one active ticket.
- **[IMPLEMENTATION_AUDIT.md](IMPLEMENTATION_AUDIT.md)** — a one-time **2026-07-10 snapshot** (module-based, not ticket-based; do not treat as current).

_Last updated: 2026-07-30._

Legend: ✅ done · 🟡 partial · ⬜ not started

| Ticket | Title | Status | Implemented | Commit | One-line summary |
|---|---|---|---|---|---|
| MVP-001 | Monorepo scaffold | ✅ | 2026-07-10 | `cf7536e` | `core/` module tree, `web/` scaffold, migrations framework, docker-compose, CI, Makefile |
| MVP-002 | Dev docker-compose stack | ✅ | 2026-07-10 (data services verified live 2026-07-22) | `cf7536e` | Postgres+Redis compose; env-prefix bug fixed & services verified healthy during MVP-011; **app containers not yet booted** |
| MVP-003 | CI pipeline | 🟡 | 2026-07-10 | `cf7536e` | `.github/workflows/ci.yml` exists; a **green run on GitHub not yet confirmed** |
| MVP-004 | Migration framework + RLS helper | ✅ | 2026-07-10 (exercised live 2026-07-22) | `cf7536e` | Alembic async `env.py` + `migrations/lib/rls.py`; proven for real by migration 001 up/down |
| MVP-005 | Config loader + error taxonomy | ✅ | 2026-07-10 | `cf7536e` | Layered `config.py`; `errors.py` RFC7807 + 12 canonical codes |
| MVP-006 | OTel tracing + structured logging | 🟡 | 2026-07-22 | `684a000` | SDK wiring (env-gated) + JSON logs + PII scrubber (tested). End-to-end trace acceptance needs webhook→consumer→send (MVP-032+) |
| MVP-007 | Health + readiness endpoints | ✅ | 2026-07-22 | `684a000` | `/healthz` (liveness) + `/readyz` (pg+redis+migration-head); compose healthcheck; tested live |
| MVP-008 | Secrets via SOPS | 🟡 | 2026-07-22 | `684a000` | Scaffold: `.sops.yaml`, gitleaks pre-commit, decrypt script, boot fail-closed (tested). Age key + real `*.enc.yaml` are founder's step |
| MVP-009 | Staging environment | 🟡 | 2026-07-22 | `684a000` | **BLOCKED** — Terraform (Hetzner CPX21) + deploy workflow written but un-applied; needs account/domain/residency (BLOCKERS #8)/Meta |
| MVP-010 | Lint guards (core↛verticals, noun/money/send) | ✅ | 2026-07-22 | `684a000` | `scripts/guards.py` + allowlist + CI wiring; 8 tests (each guard red on its violation) |
| MVP-011 | OTP auth endpoints | ✅ | 2026-07-22 | `6cd38f4` | Migration 001; OTP logic + `/v1/auth/otp(/verify)`; interim **email** channel; `EmailOtpDelivery` (gated); **verified live** |
| MVP-012 | Sessions + JWT issue/refresh | ✅ | 2026-07-29 | `35457ef` | Refresh rotation + reuse-revokes-family + rotation-race on `sessions` (001); `/v1/auth/refresh`; `jti` nonce fix; **77 pytest** live. Audit-on-reuse interim (log) until MVP-024 |
| MVP-013 | Logout + revocation | ✅ | 2026-07-29 | `35457ef` | `POST /v1/auth/logout` + `/logout-all` on `sessions` (001); revoked session can't refresh; **80 pytest** live |
| MVP-014 | Organizations + /me | ✅ | 2026-07-29 | `35457ef` | Migration 002 (orgs + user_orgs +RLS + `app.user_id` self-policy); `POST /v1/orgs`, `GET /v1/me`; refresh re-embeds org_id; `apply_rls` NULLIF-hardened; **86 pytest** live. ⚠️ RLS not enforced until `app_rw` role (BLOCKERS #11) |
| MVP-015 | RBAC roles + @requires | ✅ | 2026-07-29 | `35457ef` | Migration 003 (roles/permissions/role_permissions + user_roles+RLS, seeded); `permissions.py` constants + `requires()` dep; 403 problem+json names perm; **101 pytest** live |
| MVP-016 | Tenant middleware + app_rw | ✅ | 2026-07-29 | `290c476` | `app_rw` non-BYPASSRLS role + 2-URL split (app vs migrator); `get_db`/`org_scoped_session` SET LOCAL; session-SET guard; **RLS now ENFORCED** (BLOCKERS #11 resolved); **107 pytest** + live smoke |
| MVP-017 | Staff invite (seed only) | ✅ | 2026-07-29 | `290c476` | `invites` (global, expiring, appended after 005); owner-only invite + accept-as-staff; `invites_enabled` gate; **118 pytest** |
| MVP-018 | API keys (service auth) | ✅ | 2026-07-29 | `290c476` | Migration 004 (api_keys +RLS + `resolve_api_key` SECURITY DEFINER); `require_key_scope` sets org ctx + scope + last_used; founder-only issuance |
| MVP-019 | Messaging migration 005 | ✅ | 2026-07-29 | `290c476` | 6 org-scoped +RLS (channels/contacts/conversations/messages/templates/suppressions) + webhook_events global; isolation probed as app_rw |
| MVP-020 | Packs migration 008 + seed | ✅ | 2026-07-30 | `dbab65a` | Migration 008 (packs/pack_installations+RLS/catalog_schemas/agent_archetypes+seed/agent_bindings/agent_instances+RLS); 5 archetypes byte-for-byte vs tool-permissions.yaml (support omitted, DECISIONS); **140 pytest** |
| MVP-021 | Tenant settings service | ✅ | 2026-07-30 | `47846af` | Migration 009 (tenant_settings+RLS); `resolve()` 4-layer+provenance, tighten-only autonomy, `resolve_at`, audited writes; `/v1/settings(/effective)`; **151 pytest** |
| MVP-022 | Feature flag service | ✅ | 2026-07-30 | `47846af` | Migration 009 (feature_flags, flag_rules); in-process snapshot `eval` (no-I/O, atomic swap, sticky bucket), fail-closed kill-switch, `flag_debt.py`; `/v1/flags/eval` |
| MVP-023 | CRM migration 011 | ✅ | 2026-07-30 | `25527c0` | Migration 011 (leads/appointments/orders/attributions/segments +RLS); `last_customer_msg_at` trigger; money minor-units; **156 pytest** |
| MVP-058 | Prompt registry | ✅ | 2026-07-30 | `25527c0` | Migration 010 (prompt_layers partial-RLS+content-immutable, prompt_bindings+RLS one-active, prompt_evals); `registry.py` pin/compat/revert; HTTP endpoints deferred |
| MVP-026 | Consumer framework | ✅ | 2026-07-30 | `ee2ed43` | `@consumer` + XREADGROUP/XAUTOCLAIM drain, graceful shutdown; **168 pytest** |
| MVP-027 | Consumer dedupe | ✅ | 2026-07-30 | `ee2ed43` | `dedupe_consumer` insert-first → exactly-once effect; 30d prune |
| MVP-028 | Scheduler process | ✅ | 2026-07-30 | `ee2ed43` | cron matcher (no croniter) + tz-local firing + per-(job,minute) redis lock |
| MVP-029 | Retries + DLQ | ✅ | 2026-07-30 | `ee2ed43` | bounded retries → `gop:dlq:<type>` + alert.ops; `dlq-replay.py` |
| MVP-030 | Typed event catalog | ✅ | 2026-07-30 | `ee2ed43` | `gen_events.py` → `types.py` (specs+checksum) drift test; `emit()` validates payloads |
| MVP-032 | Webhook ingress + sig verify | ✅ | 2026-07-30 | `33a76bc` | `/webhooks/whatsapp` constant-time HMAC verify + dedupe + quarantine, always-200; **174 pytest** |
| MVP-033 | Message normalizer | ✅ | 2026-07-30 | `33a76bc` | webhook→contact/conversation/message + `msg.received.v1` emit; `resolve_channel` fn; idempotent |
| MVP-031 | WhatsApp WABA connect | ✅ | 2026-07-30 | `2160890` | Migration `cfd462c65ec9` (`channel_credentials`+RLS); `POST /v1/channels/whatsapp/connect` token/handshake/echo gates (gated-simulated), Fernet-encrypted creds at rest, cross-org 409, health probe; **183 pytest** (merge `644b334`) |
| MVP-034 | Send adapter (gated) | ✅ | 2026-07-30 | `1cd8615` | `send.py`: single outbound exit, 4 fail-closed gates (audit capability / execution token / suppression / consent), 429 Retry-After + 5xx×3 retries, `msg.sent`/`msg.failed` + audit outcome; no migration; **191 pytest** (merge `59eb28b`) |
| MVP-047 | Text search (BM25) | ✅ | 2026-07-31 | _uncommitted_ | `search.py` search_text tsvector (simple⊕english) from title/desc/x-search projection, maintained on write; `ts_rank` BM25 over GIN; `GET /v1/catalog/search`; **318 pytest** |
| MVP-046 | Attributes validation (JSON Schema + CEL) | ✅ | 2026-07-31 | `63cb525` | `validate.py` Draft-2020-12 (+additionalProperties) + CEL constraints → `{path,error,rule}`, cached per (pack,ver); wired into CRUD → 422; **315 pytest** |
| MVP-045 | Catalog migration + CRUD | ✅ | 2026-07-31 | `170eec0` | Migration `d2cecc53f63c` (012: catalog_items+history+idempotency, RLS, GIN+HNSW, pgvector); `crud.py` create/get/list-cursor/update-ifmatch/soft-delete + history + identity dedup + Idempotency-Key; `POST/GET/PATCH/DELETE /v1/catalog/items`; **307 pytest** (unblocks 042) |
| MVP-043 | Kirana dry-run CI gate | ✅ | 2026-07-31 | `b70a4b1` | `installer.dry_run` (full pipeline in a rolled-back txn, validates+plans, no writes); `verticals/kirana/install.yaml` + `tests/e2e/test_kirana_dryrun.py`; CI gate proves 2nd pack installs with zero core changes; **291 pytest** |
| MVP-041 | Jewelry install e2e fixture | ✅ | 2026-07-31 | `527412a` | `verticals/jewelry/install.yaml` + `tests/e2e/test_jewelry_install.py` (assert expected_result field-by-field, <60s); wired into CI `migrate` job (app_rw + e2e) as a required check; **290 pytest** |
| MVP-040 | Transactional installer + API | ✅ | 2026-07-31 | `9fa3ac3` | `installer.py` 6-step atomic pipeline (2 deferred #14) + digest idempotency + rollback + uninstall; migration `5dcbda42efca` (+`failed` status); `GET/POST/DELETE /v1/packs(/installations)`; **289 pytest** |
| MVP-039 | Bundle parser + verifier | ✅ | 2026-07-31 | `db28412` | `core/packs/bundle.py`: prompt anchor split → layer records, `parse_pack_dir` (per-file contract validation, path+file-named errors), digest manifest + ed25519 verify (tampered refused), `load_bundle` dev/prod; no new deps (.tar.zst deferred #13); **279 pytest** |
| MVP-038 | Pack contract models | ✅ | 2026-07-31 | `9cda64f` | `core/packs/contracts.py` typed L0↔L1 contracts (manifest+slots, bindings, catalog, pricing, workflow, integration + aux); strict/open split; every verticals/* file parses + path-precise negatives; **266 pytest** |
| MVP-037 | Media handling | ✅ | 2026-07-31 | `b0a3bd0` | `media.py` inbound mime/size gates + fail-closed AV scan (quarantine+`alert.ops`) + object store → `messages.media`; outbound upload helper; **simulated** scanner/store (no new deps, real clamav/MinIO gated — BLOCKERS #12); normalizer wired; **225 pytest** |
| MVP-035 | Templates management | ✅ | 2026-07-31 | `1eb7f5f` | Migration `83efabba79ee` (message_templates Meta cols + `channels.waba_id` + `resolve_channel_by_waba`); `templates.py` CRUD/submit/status/gate/drainer; `send()` template path; `GET /v1/channels/whatsapp/templates`; jewelry_v2 seed YAML + gated script; **214 pytest** (merge `a06be80`). Real Meta submit/webhooks gated (#3) |
| MVP-036 | Suppression + consent | ✅ | 2026-07-30 | `9e3a11d` | Enforcement gate in MVP-034 + `keywords.py` STOP/UNSUB net (en/hi/te) → normalizer auto-suppress (marketing, idempotent) + founder-approved transactional confirm (DECISIONS 2026-07-30); **206 pytest** (merge `301edf3`). Suppressed badge deferred to chats page (MVP-087) |
| MVP-024 | Audit chain writer | ✅ | 2026-07-30 | `dbab65a` | Migration 006 (audit_log append-only +RLS +trigger, dedupe_consumer); per-org hash chain writer + 10m capability + `audit-verify.py`; p95 **1.12ms**; **131 pytest** |
| MVP-025 | Outbox emit + publisher | ✅ | 2026-07-30 | `dbab65a` | Migration 007 (event_outbox global); `emit` same-txn + `publish_batch` at-least-once → Redis streams (CloudEvents); crash-window + idempotency; **137 pytest** |

> **Gap note (resolved 2026-07-22):** MVP-006–010 were leapfrogged after the scaffold; the
> founder directed implementing them before continuing. Now done/scaffolded on branch
> `feature/mvp-006-010-platform-foundations`: **007 + 010 fully; 006 + 008 partial** (downstream
> deps / founder key); **009 BLOCKED** (needs Hetzner account, domain, residency decision, Meta).

---

## What each completed ticket actually delivered

### MVP-001–005 — Monorepo scaffold (2026-07-10, `cf7536e`)
Bundled in one commit per the scaffold prompt:
- **001** — repo layout: full `core/` module tree (17 submodules as stubs), `web/` (Vite + React + TS + Tailwind), `migrations/`, `infra/docker/`, `Makefile`, `pyproject.toml`.
- **002** — `infra/docker/docker-compose.dev.yml`: Postgres (pgvector) + Redis + api/worker/scheduler/caddy.
- **003** — `.github/workflows/ci.yml`.
- **004** — Alembic async migration framework + `migrations/lib/rls.py` (`apply_rls`/`drop_rls`).
- **005** — `core/common/config.py` (layered settings) + `core/common/errors.py` (RFC7807 + the 12 canonical error codes).

At the time, 002–005 were "present but not fully verified." Since then, MVP-011 verified 002 (data services) and 004 (migration up/down) for real against a live database.

### MVP-011 — OTP auth endpoints (2026-07-22, `6cd38f4`)
- **Migration 001** — global `users`, `sessions`, `otp_challenges` (no RLS; membership deferred to `user_orgs`/002). Verified live: upgrade + downgrade round-trip.
- **Auth logic** (`core/tenancy/auth.py`) — E.164 + email validation, argon2 hashing, OTP challenge state machine (5m expiry / ≤5 attempts / 60s resend), JWT mint/decode (15m access / 30d refresh).
- **API** — `POST /v1/auth/otp`, `POST /v1/auth/otp/verify`; failures use plain HTTP (401/422/429), never canonical error codes.
- **Interim email channel** — `GROWTH_OPERATOR_OTP_CHANNEL` (default `email`) because Meta WhatsApp is **pending API access, not deferred**; phone path retained behind the flag.
- **`EmailOtpDelivery`** — SMTP adapter, **gated OFF by default** (real send needs provider + creds + enable).
- **Tests** — 45 unit + 3 live-DB integration (request→verify→tokens, wrong-code, lockout).
- Also on this branch (not separate tickets): docker-compose env-fix (BLOCKERS #1), a login-page demo (MVP-082 preview slice), and the live-DB integration harness.

---

## Process (per founder request, 2026-07-22)
Every completed ticket gets **a dated row here + a dated entry in IMPLEMENTATION_LOG.md**, using the actual calendar date of implementation. Update this file the day work lands.
