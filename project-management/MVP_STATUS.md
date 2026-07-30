# MVP Ticket Status Ledger

One scannable row per ticket, **with the date it was implemented**. This is the quick
index; the sources of truth stay:

- **[IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md)** — full dated detail per completed ticket (append-only).
- **[CURRENT_TASK.md](CURRENT_TASK.md)** — the one active ticket.
- **[IMPLEMENTATION_AUDIT.md](IMPLEMENTATION_AUDIT.md)** — a one-time **2026-07-10 snapshot** (module-based, not ticket-based; do not treat as current).

_Last updated: 2026-07-29._

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
| MVP-020 | Packs migration 008 | ⬜ | — | — | Deferred behind 024/025 (migration-chain order) |

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
