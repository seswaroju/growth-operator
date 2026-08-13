# Current Task

This file always describes exactly one active ticket. When a ticket completes, append its verified summary to
`IMPLEMENTATION_LOG.md` and mark this task as
`Completed — awaiting founder review`.

Do not replace this file with a new ticket until the founder explicitly
selects and approves the next ticket.

---
## PILOT-1C · Complete ghost-recovery vertical slice — **Completed — awaiting founder review** (2026-08-13)

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

**Requires founder attention:**
1. `negotiating` was requested as a recovery trigger stage but `leads.stage` does not permit it
   (new/qualified/quoted/visit_booked/won/lost). `ENGAGED_STAGES` is now `("quoted",)` — the only
   value that ever actually matched. Adding `negotiating` is a CRM stage change.
2. Three `tests/integration/test_rate_ingestion.py` failures are pre-existing on `main` and
   unrelated to this ticket (verified by checkout).

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
