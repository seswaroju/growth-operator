# Deferred / Add-Back TODO

Interim shortcuts taken to avoid long external lead-times (chiefly Meta WABA
verification). **Each item here must be reversed or completed before pilot go-live** —
do not let an interim shortcut silently become permanent. Cross-reference:
[BLOCKERS.md](BLOCKERS.md), [DECISIONS.md](DECISIONS.md).

Created 2026-07-22 during MVP-011.

---

## 1. Real Meta WhatsApp Business API (WABA) — IN PROGRESS, awaiting API access

- **Status:** NOT deferred — actively wanted and on the critical path; blocked only on
  Meta granting API access (verification in flight). Number decided: Srila's existing
  WhatsApp number (DECISIONS.md 2026-07-22). This is the long-pole external item — track
  the access status.
- **Interim bridge (not a replacement):** email OTP (item 2) lets owner login work
  end-to-end while access is pending; swap OTP delivery to WhatsApp once access lands.
- **When access arrives:** implement the Meta send/ingress adapter (MVP-031..037) and
  point channel config at Meta; restore phone OTP (item 2).
- **Affects:** MVP-011 (OTP delivery), MVP-031..037 (WhatsApp channel).

## 2. OTP delivery channel — INTERIM = EMAIL (decided 2026-07-22)

- **Spec baseline:** `docs/25-implementation-starter-kit/13-auth-rbac-approval-audit.md`
  specifies **Phone OTP**. The interim deviates to avoid Meta.
- **Interim choice:** **email** replaces phone as the login identifier
  (`GROWTH_OPERATOR_OTP_CHANNEL=email`, the default). The phone-OTP code path is retained
  behind that flag — **not deleted** — set it to `phone` to restore.
- **Still needed for real email delivery:** a transactional email provider + credentials
  + founder approval (§10.4). The `EmailOtpDelivery` (SMTP) adapter is now **written but
  gated OFF** (`GROWTH_OPERATOR_OTP_EMAIL_ENABLED=false` default; startup fails if enabled
  without full SMTP config). To go live in staging: pick an SMTP provider, set
  `OTP_EMAIL_ENABLED=true` + `SMTP_HOST`/`SMTP_FROM`/`SMTP_USERNAME`/`SMTP_PASSWORD`
  (password via SOPS secrets). Local testing works via dev echo
  (`GROWTH_OPERATOR_OTP_DEV_ECHO=true`, dev env only).
- **Add back:** restore phone OTP as the primary channel (`OTP_CHANNEL=phone`) once
  Meta/BSP delivery is live; the `(channel, identifier)` model already supports running
  both channels at once if desired.

## 3. MVP-011 acceptance criterion — "OTP on founder's real phone in staging"

- **Status:** BLOCKED as written (needs Meta + staging).
- **Interim equivalent:** "OTP delivered to founder's real _{email|phone}_ in staging"
  via the interim provider — satisfies the same end-to-end delivery gate.
- **Add back:** re-run the original phone-in-staging smoke test once Meta is live.

## 4. Interim messaging provider (conversation channel) — future

- If a third-party BSP replaces Meta for the **conversation channel** (MVP-031+, not yet
  in scope), track swapping it back to the direct Meta adapter here when Meta is ready.

## 5. MVP-006 completion (OTel) — partial, added 2026-07-22

- **Done:** env-gated tracer/OTLP, FastAPI instrumentation, JSON logs + PII scrubber (tested).
- **Deferred:** the `opentelemetry-instrumentation-{asyncpg,redis,httpx}` packages were
  removed until a real tracing backend (Grafana Cloud) exists — re-add them to
  `pyproject.toml` to activate the guarded instrumentors in `telemetry._instrument()`.
- **Add back:** a tracing backend + the "one trace: webhook→consumer→send" continuity test
  (blocked on MVP-032+ components existing), and wire `run_id` span attributes at MVP-055.

## 6. MVP-008 completion (SOPS) — scaffold, added 2026-07-22

- **Done:** `.sops.yaml`, gitleaks pre-commit config, `secrets/` README + fake example,
  `decrypt-secrets.sh`, boot fail-closed (`assert_secrets_available`, tested).
- **Founder step (§10.1):** `age-keygen`, paste the public key into `.sops.yaml`, create the
  real `secrets/{dev,staging,prod}.enc.yaml`, and run `pre-commit install` so gitleaks blocks
  plaintext-secret commits. Then set `GROWTH_OPERATOR_REQUIRE_SECRETS_FILE=true` in staging/prod.
