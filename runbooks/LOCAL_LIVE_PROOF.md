# Local live proof (PILOT-1D-L) — founder runbook

Prove Vaylorn against **real** external providers on the Mac mini, before paying for a VPS.

The Mac mini is a development environment and a live-integration proving ground. It is **not**
merchant hosting: a Quick Tunnel URL changes every restart, there is no TLS you control, no backups
and no uptime. Nothing here should ever be described as production.

Each step is gated on you supplying a credential and saying go. Nothing external happens otherwise.

---

## Why `env=dev`, not `env=prod`

Deliberate, and the opposite of sloppy.

`env=prod` triggers the PILOT-1A startup validator, which refuses to boot on any of eleven
repository defaults. Satisfying it locally would mean generating production-grade signing keys and
a SOPS secrets file for a laptop test — or, far worse, weakening the validator until the laptop
passed. That validator is the only thing standing between a forgotten environment variable and a
system that signs sessions with a constant published in this repository.

So this stays `env=dev`, with **exactly two** real-provider switches turned on, one at a time. The
production validator is untouched.

---

## Configuration

Local values go in `.env` at the repository root — **gitignored, never committed**.

```bash
# .env — LOCAL ONLY
GROWTH_OPERATOR_ENV=dev

# Step 1: real LLM
GROWTH_OPERATOR_LLM_PROVIDER_ENABLED=true
GROWTH_OPERATOR_LLM_KEY_DEEPSEEK=<founder-supplied>

# Step 2: real Meta (only after step 1 passes)
GROWTH_OPERATOR_WHATSAPP_LIVE_ENABLED=true
GROWTH_OPERATOR_WHATSAPP_APP_SECRET=<from the Meta app>
GROWTH_OPERATOR_WHATSAPP_VERIFY_TOKEN=<any long random string; also typed into Meta>

# OTP stays echoed locally — dev only, refused outside dev by assert_otp_config_safe
GROWTH_OPERATOR_OTP_DEV_ECHO=true
```

Both switches default to **false**. With them off every adapter is simulated and no packet leaves
the machine, which is why the ordinary test suite never makes a network call.

**Secrets hygiene:** put keys in `.env`, not on the command line — a shell history is a file. Never
paste a key into a document, an issue, or a commit message. `gitleaks` runs pre-commit and in CI.

---

## Step 1 — real LLM

*Needs: one provider key. Cost: fractions of a cent.*

```bash
uv run python scripts/eval_models.py --live --provider deepseek --model deepseek-v4-flash
```

Records per case: provider, exact model, latency, tokens, estimated cost, groundedness, and any
unsupported claim. `--live` is the only way real calls happen; the default transport is mocked and
CI never pays a vendor.

**Quality cases** (§6), all with controlled test data:

| # | Input | Must hold |
|---|---|---|
| A | asks for a product that **exists** | answer grounded in catalog evidence |
| B | asks for a product that **does not exist** | does not invent one |
| C | asks a price | deterministic pricing/ledger rules govern, not the model |
| D | catalog text containing injection | evidence stays untrusted data, not instructions |
| E | ghost diagnosis | returns a **structured** result validated against the pack taxonomy |

Case E has a second value: an invented reason is dropped by `parse_diagnosis` and abstains to the
owner. Watching a real model hit that path is worth more than any unit test of it.

✅ **Closes the PILOT-1B physical gap.**

---

## Step 2 — Cloudflare Tunnel

*Needs: `brew install cloudflared` (not vendored — a Cloudflare binary has no business in this
repository).*

```bash
make dev                                          # api :8000, worker, scheduler, postgres, redis
uv run python scripts/webhook_ingress.py          # webhook-only ingress on :8080
cloudflared tunnel --url http://localhost:8080    # NOTE: 8080, not 8000
```

**Point the tunnel at 8080, never 8000.** The application publishes 143 paths, including
`/v1/admin/tenants`, `/docs`, `/openapi.json` and the OTP routes. Tunnelling it directly would put
all of them on a public HTTPS URL — and a random URL is not an access control: it appears in
Cloudflare's logs, in Meta's configuration, and in whatever terminal printed it.

The ingress publishes exactly one path and two methods (`GET`/`POST /webhooks/whatsapp`) and returns
a bare 404 for everything else. It forwards the raw body byte-for-byte and the
`X-Hub-Signature-256` header untouched — Meta signs the raw bytes, so any re-encoding would make
every real webhook fail as a forgery. It authenticates nothing itself; verification stays in the
application, where it belongs.

Prints `https://<random>.trycloudflare.com`. Outbound connection only — no router change, no port
forwarding. Postgres and Redis stay bound to localhost and are never published.

**The URL changes on every restart.** Reconfigure Meta's callback each session: annoying, and the
correct trade for a test-only tunnel. Stop the tunnel when the session ends.

---

## Step 3 — Meta test assets

*Founder actions. Do not invent values.*

- [ ] Meta developer account, app, WhatsApp product added
- [ ] **test phone number** Meta issues (not the merchant's line)
- [ ] temporary access token
- [ ] **your handset added as a permitted test recipient** — Meta refuses others
- [ ] App Secret → `GROWTH_OPERATOR_WHATSAPP_APP_SECRET`
- [ ] verify token you choose → both `.env` and Meta

Callback URL: `https://<tunnel>/webhooks/whatsapp`, subscribe **messages**. That path is the only
one the tunnel serves; anything else Meta (or anyone else) requests returns 404.

Meta calls `GET` with `hub.verify_token`; Vaylorn echoes the challenge only on an exact match. Every
`POST` is HMAC-SHA256 verified against the app secret over the raw body — the tunnel proves nothing
about authenticity and is not trusted to.

---

## Step 4 — first real inbound

Message the Meta test number from your handset.

```
handset → Meta → tunnel → ingress :8080 → app :8000/webhooks/whatsapp → webhook_events → normalizer → contact/conversation/message
```

Verify: signature accepted, `webhook_events` row, contact + conversation + message under the right
org, and a **redelivery of the same event creates nothing new** (dedupe by external id).

Record it as `REAL_META`. A locally-crafted request is not this.

---

## Step 5 — pilot tenant

Everything needed already exists; no new code.

1. Log in locally (OTP echoed to the terminal in dev), then `make grant-admin EMAIL=you@…`
2. `POST /v1/admin/tenants` — creates org + owner + subscription, installs the jewelry pack, and
   activates the plan's agents in one call
3. `POST /v1/catalog/items` — a handful of real, controlled items
4. `POST /v1/channels/whatsapp/connect` — the Meta test `waba_id`, `phone_number_id`, token

---

## Step 6 — real Priya round trip

Ask a catalog question from your handset.

```
handset → Meta → tunnel → persistence → Priya → catalog retrieval via mediation → real LLM
 → grounded answer → safety/approval → real Meta outbound → handset
```

Capture: tenant, conversation, inbound message, agent run, provider, exact model, evidence/catalog
ids, outbound message, **wamid**, status.

A `wamid.SIM-*` means the simulator ran and the proof did not happen.

---

## Step 7 — recovery

Two independent results. **Do not conflate them.**

### 7A — engine, with a real LLM

Create a controlled `quoted` lead and age it past the silence threshold, then let production code
run untouched: `recovery_sweep` → `lead.went_silent.v1` → workflow → **real** diagnosis → owner
approval → recovery attempt → send boundary.

Nothing is bypassed: guards, consent, suppression, entitlement, mediation and attempt idempotency
all apply. If `pilot_recovery_check_in` is not yet approved, the send refuses with
`template_not_sendable` — **that is a pass for 7A**, not a failure. The engine reached the boundary
and the gate held.

→ `RECOVERY ENGINE LIVE-LLM PROVEN`

### 7B — Meta transport

Separately, send an approved Meta sample template to your handset through the real `messages.send`
path.

→ `META REAL TRANSPORT PROVEN`

**Neither is `EXACT VAYLORN RECOVERY TEMPLATE PROVEN`.** That needs `pilot_recovery_check_in`
approved, a real dispatch, a delivery receipt, your reply, and `recovery_attempt.status = replied`.
Only then is the physical PILOT-1C gap closed.

---

## Scoreboard

| Claim | Status |
|---|---|
| Real LLM provider works | ☐ |
| Provider-agnostic Priya makes a real call | ☐ |
| Meta test Cloud API works | ☐ |
| Real webhook reaches the Mac over HTTPS | ☐ |
| Tunnel published the webhook path only | ☐ |
| Handset receives a real WhatsApp message | ☐ |
| Handset reply reaches Vaylorn | ☐ |
| Real Priya conversational round trip | ☐ |
| Recovery engine live-LLM proven | ☐ |
| Exact Vaylorn recovery template proven | ☐ |

Simulated models, `wamid.SIM-*`, mocked HTTP and any test-suite result count for **none** of these.
