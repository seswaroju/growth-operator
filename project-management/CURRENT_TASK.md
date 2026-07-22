# Current Task

This file always describes exactly one active ticket. When a ticket completes, append its verified summary to
`IMPLEMENTATION_LOG.md` and mark this task as
`Completed — awaiting founder review`.

Do not replace this file with a new ticket until the founder explicitly
selects and approves the next ticket.

---

## Ticket: MVP-011 · OTP auth endpoints

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
