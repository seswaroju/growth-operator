# Current Task

This file always describes exactly one active ticket. When a ticket completes, append its verified summary to
`IMPLEMENTATION_LOG.md` and mark this task as
`Completed — awaiting founder review`.

Do not replace this file with a new ticket until the founder explicitly
selects and approves the next ticket.

---
## PILOT-1D-L · Local live proof — **REPOSITORY READY** (2026-08-14)

    PILOT-1D-L   REPOSITORY READY = YES
                 REAL LLM PROVEN = NO
                 META TRANSPORT PROVEN = NO
                 REAL PRIYA ROUNDTRIP PROVEN = NO

Branch `feature/pilot-1d-l-local-live-proof`. **No migration. No external call of any kind** — no
LLM request, no Meta call, no tunnel started, no VPS, no DNS, no message to anyone.

Prepared: `scripts/webhook_ingress.py` (publishes only `GET|POST /webhooks/whatsapp`; the
application's other 142 paths return 404), `scripts/bootstrap-host.sh` (one command for a fresh
Ubuntu VPS), `runbooks/FIRST_PILOT_DEPLOY.md`, `runbooks/LOCAL_LIVE_PROOF.md`.

Runbooks live in the repository, not `docs/` — that is a read-only symlink to the vault (CLAUDE.md
§4), and a runbook followed while a merchant waits should sit with the scripts it invokes.

**Measured cutover dry-run** on a disposable Ubuntu container, pruned builder cache: ~66s of
software deployment end to end (packages 14s, sops 2s, Docker 17s, cold image build 20s, DB init +
role 4s, 70 migrations 1s, API ready 2s, worker 6s), `/readyz` reporting postgres, redis and
migration_head all true. A 2 vCPU droplet will be several times slower and still well inside the
30-minute target.

**Next:** founder supplies one LLM key, then one step at a time per the local live-proof runbook.

---
## PILOT-1A · Live Vaylorn pilot environment (engineering prerequisites) — **CODE-COMPLETE** (2026-08-14)

    PILOT-1A   CODE-COMPLETE = YES   DEPLOYED = NO
    PILOT-1B   CODE-COMPLETE = YES   REAL LLM PROVEN = NO
    PILOT-1C   CODE-COMPLETE = YES   PHYSICAL RECOVERY PROVEN = NO
    PILOT-1D   NOT COMPLETE — founder/external activation next

**Vaylorn is not live.** Production deployment artifacts existing is not the same as a deployment
existing. No droplet, no DNS, no secrets on a host, no Meta assets, no live LLM call, no message
sent to anyone.

Merged `de2c55b`; final main `3215b38`; migration head `d53fdc8c9b82`; CI `31805475813` and Deploy
staging `31805475778` both green.

Branch `feature/pilot-1a-live-environment`. **Migration 054** (repoint routes off retired models).

Prepares deployment; provisions nothing. No droplet, no DNS, no Meta resources, no external sends,
no paid calls — all founder-owned and untouched.

**Three findings worth the founder's attention:**
1. **Both Anthropic models in the registry were RETIRED**, not merely stale — Sonnet 3.5 on
   2025-10-28, Haiku 3.5 on 2026-02-19 — and four database routes pointed at them. The first real
   API call would have failed during the first live smoke. Migration 052 had "fixed" these ids once
   by adding date suffixes, which made them well-formed and no more callable.
2. **`roles.sql` hardcodes `PASSWORD 'app_rw'`.** Mounting it in production would have created the
   runtime role with a credential published in this repository. Production now uses
   `roles-prod.sh`, and the deploy verifies `app_rw` exists AND lacks BYPASSRLS.
3. **The fallback chain named a retired model**, so the fail-safe would itself have failed.

**Still founder-blocked (unchanged):** droplet, DNS, age key + `secrets/prod.enc.yaml`, SMTP
account, LLM key(s), Meta assets. See the activation report.

---
## PILOT-1C · Complete ghost-recovery vertical slice — **COMPLETE (code)** (2026-08-13)

**Merged to `main` and CI green.** Merge `5c531f1`; `main` at `32cb1f5`; migration head
`05ee829beb92`. Physical WhatsApp recovery proof deferred to PILOT-1D/1E (BLOCKERS #37).

Branch `feature/pilot-1c-ghost-recovery`. **Migration 053** (`recovery_attempts`, message
idempotency, worker-authority provenance).

**The gap this closes:** the recovery playbook reasoned, asked the owner, composed a message — and
then waited 96 hours for a reply to something nobody had sent. No workflow step could cause an
external effect, so the workflow could complete "successfully" having done nothing. The slice is now
end to end: silence detected → grounded diagnosis → owner decision → approved template →
mediated WhatsApp send → delivery status → reply → measurable outcome.

**Live-proven vs code-complete.** Every gate, guard, RLS boundary, idempotency claim and lifecycle
transition is proven against real Postgres (1838 tests). The one thing NOT physically proven is a
real message reaching a real phone: that needs Meta credentials and a founder-approved external
send, which §10.4 reserves. The send path is exercised to the provider boundary and no further.

**Founder rulings applied:**
1. `ENGAGED_STAGES = ("quoted",)` accepted for Pilot-1 — `quoted` is the minimum defensible wedge
   because the system has concrete evidence the merchant made a commercial response. `negotiating`
   is recorded as later CRM/product-depth work, NOT a Pilot-1C blocker (BLOCKERS #35).
2. Pre-existing rate-ingestion failures left untouched and reported separately (BLOCKERS #36).
3. PILOT-1C closes as CODE-COMPLETE only. NOT physically live-proven (BLOCKERS #37).

**Found and fixed during post-merge verification** (both merged, both CI-green):
* Reply correlation compared a Postgres-written `sent_at` against a worker-host timestamp — two
  clocks that are never equal. A worker lagging the database would have under-reported recoveries
  silently. Now one clock domain, `clock_timestamp()`.
* Two test fixtures depended on ambient database state and went red on CI's fresh database. Both
  now build their own preconditions.

---

## PILOT-1B · Provider-agnostic real Priya runtime + grounding — **COMPLETE (code)** (2026-08-13)

Real-provider smoke test deferred to PILOT-1D/1E.

---

## PILOT-1B · Provider-agnostic real Priya runtime + grounding — **Completed — awaiting founder review** (2026-08-13)

Branch `feature/pilot-1b-provider-agnostic-runtime`. **Migration 052** (additive telemetry + a data
correction).

**The bug this closes:** provider selection was cosmetic. `llm_client` read a single global
`llm_provider`/`llm_api_key`/`llm_api_base`, so assigning GPT-4o to a store sent a Claude-shaped
request to Anthropic, and "fallback" re-hit the same vendor with the same key. Every call now
resolves its own adapter, endpoint and credential.

- `core/runtime/providers.py` — approved vendors, platform-controlled endpoints, per-provider
  `credential_ref`. **openai · deepseek · anthropic**, with DeepSeek proving the `openai_compatible`
  transport works against a non-OpenAI vendor.
- `core/runtime/model_registry.py` — approved models with capabilities, context, quality tier and
  **exact per-model pricing** (gpt-4o vs gpt-4o-mini differ 15×; the old per-provider table priced
  them identically). The adapter belongs to the provider, never the model.
- `core/runtime/adapters/` — `openai_compatible` (two vendors, one implementation) and
  `anthropic_native`. `NormalizedResult` can carry tool-call proposals later without reshaping.
- `core/runtime/grounding.py` — retrieval-before-generation. Catalog text is fenced, marker-stripped
  and length-bounded; a deterministic check abstains on unsupported product/price claims. **No
  second LLM judge.**
- `costs_lite` gains `latency_ms`, `error_class`, `attempt_index` (0 = primary).
- `scripts/eval_models.py` — same corpus across candidates, mocked by default, `--live` opt-in.

**No permanent default model is declared** — that is an operational decision from eval results.

**Do not start PILOT-1C or any later ticket automatically.**

## PILOT-1B · Provider-agnostic real Priya runtime + grounding — **Completed — awaiting founder review** (2026-08-13)

Branch `feature/pilot-1b-provider-agnostic-runtime`. **Migration 052** (additive telemetry + a data
correction).

**The bug this closes:** provider selection was cosmetic. `llm_client` read a single global
`llm_provider`/`llm_api_key`/`llm_api_base`, so assigning GPT-4o to a store sent a Claude-shaped
request to Anthropic, and "fallback" re-hit the same vendor with the same key. Every call now
resolves its own adapter, endpoint and credential.

- `core/runtime/providers.py` — approved vendors, platform-controlled endpoints, per-provider
  `credential_ref`. **openai · deepseek · anthropic**, with DeepSeek proving the `openai_compatible`
  transport works against a non-OpenAI vendor.
- `core/runtime/model_registry.py` — approved models with capabilities, context, quality tier and
  **exact per-model pricing** (gpt-4o vs gpt-4o-mini differ 15×; the old per-provider table priced
  them identically). The adapter belongs to the provider, never the model.
- `core/runtime/adapters/` — `openai_compatible` (two vendors, one implementation) and
  `anthropic_native`. `NormalizedResult` can carry tool-call proposals later without reshaping.
- `core/runtime/grounding.py` — retrieval-before-generation. Catalog text is fenced, marker-stripped
  and length-bounded; a deterministic check abstains on unsupported product/price claims. **No
  second LLM judge.**
- `costs_lite` gains `latency_ms`, `error_class`, `attempt_index` (0 = primary).
- `scripts/eval_models.py` — same corpus across candidates, mocked by default, `--live` opt-in.

**No permanent default model is declared** — that is an operational decision from eval results.

**Do not start PILOT-1C or any later ticket automatically.**
