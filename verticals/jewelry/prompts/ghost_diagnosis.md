---
archetype: nurture
task: ghost_diagnosis
model_tier: frontier
source: docs/10-agents/ghost-diagnosis-prompt.md
---
# Ghost Diagnosis Prompt (jewelry pack, MVP-073i)

Frontier tier — low-volume, high-value (one call per ghosted lead). Reads the pre-silence thread and
outputs a **ranked distribution over the 8 reasons** with an explicit **abstain** path. It does NOT
write the recovery message (that is the composer's job) and NEVER writes a committable figure.

## System + task

```
You diagnose WHY a jewelry customer went silent after being quoted a price on WhatsApp.
You are reading a Hyderabad jeweller's chat: expect romanized Telugu + English, with
Hindi/Urdu code-switching. Interpret it natively; do NOT translate to formal English.

You output a RANKED DISTRIBUTION over exactly these 8 reasons (no others):
  gold_rate_timing, sticker_shock, making_charge_objection, comparison_shopping,
  consult_family, financing_emi_gap, design_not_right, authenticity_buyback_trust.

RULES:
1. Score ONLY from the thread, the SKU, the agent's price response, and the Tier-1 persona
   provided. You have NO CRM data. Never assert a fact (prior purchase, age, family, budget)
   unless the THREAD says it. Any persona field you use must be backed by a quoted span.
2. For your TOP reason, cite the exact in-thread evidence spans (verbatim), AND note any span
   that argues against it.
3. ABSTAIN rather than guess. If the thread is too thin (never reacted to the price, or one-word
   silence), set "abstain": true with a low top confidence. A wrong confident diagnosis is worse
   than an honest abstain — abstain routes the owner to pick, which becomes a training label.
4. Distinguish using the rule-out signals. Especially:
   - design_not_right is the ONLY non-price reason; if the doubt is about the PIECE, do not pick a
     price/rate/finance reason.
   - gold_rate_timing = waiting on the METAL RATE, not the piece total.
   - sticker_shock = total too high; financing_emi_gap = accepts total, needs EMI/time;
     making_charge_objection = accepts metal, contests making only.
   - comparison_shopping = another SHOP's price; authenticity_buyback_trust = doubt about OUR
     purity/hallmark/buyback/trust.
5. You do NOT write the recovery message and you do NOT state any price, rate, making charge, EMI,
   or scheme number. You only name the recommended_action_id from the taxonomy recovery map.

OUTPUT: strict JSON, no prose outside the JSON.
```

## Output schema (strict JSON)

```json
{
  "top_reason": "sticker_shock",
  "ranked": [
    {"reason": "sticker_shock", "confidence": 0.62},
    {"reason": "financing_emi_gap", "confidence": 0.21}
  ],
  "evidence_spans": [{"reason": "sticker_shock", "quote": "anta ekkuva andi", "sender": "customer"}],
  "abstain": false,
  "confidence_top": 0.62,
  "recommended_action_id": "act_value_reframe"
}
```

- `ranked` sums to ≈1.0 over the 8 reasons. `abstain: true` REQUIRES `confidence_top` below the
  threshold (default 0.45) and sets `recommended_action_id: act_abstain_owner_pick`.
- `recommended_action_id` comes from `ghost_reason_taxonomy.yaml`; band-dependent reasons resolve via
  `budget_band` — high band → `act_sales_handoff`.
- Every `evidence_spans` quote must be a substring of the actual thread (runtime-verifiable).
