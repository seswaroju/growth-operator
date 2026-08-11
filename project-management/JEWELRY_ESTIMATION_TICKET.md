# Ticket JWL-EST-01 — Jewelry-style customer estimation (itemized), not a flat price

**Status:** Spec refined with founder (2026-08-10); **2 design questions open**. Parked until the OC5–OC12 run reaches a stopping point / founder schedules it.
**Track:** Jewelry vertical pack (L1) + catalog + customer-facing draft. **Not** operator-console (OC).

---

## 1. Founder request

> "When a customer asks about an item, give a jewelry **estimation** (grams/karat of gold, making
> charges, CGST, SGST, labor if any), not just a price."

## 2. What already exists (scope is narrow — mostly pack config + draft wiring)

- **Pricing engine** (`core/pricing/engine.py`) runs strategy stages → **itemized breakdown + total**,
  ledgered, exact arithmetic.
- **Jewelry strategy** (`verticals/jewelry/pricing/strategy.yaml`) already computes: metal_value
  (net_weight_g × live rate/g for the purity) → wastage → **making** → stones → subtotal → discount →
  **GST 3%** → total.
- **Catalog schema** (`verticals/jewelry/catalog/schema.json`) already carries **purity (24K/22K/…),
  gross_weight_g, net_weight_g, stone_details, huid, category**.
- **Quote card template** (`verticals/jewelry/ui/templates.yaml`) already renders breakdown rows:
  metal / making / stones / **GST (3%)**.

So the estimate is for a **specific catalog item** (known weight + purity/karat) and the math + a quote
card already exist. The work is per-product controls + a CGST/SGST split + a two-step customer flow.

## 3. Founder decisions (2026-08-10) — DECIDED

1. **No IGST.** Only **CGST + SGST** (India, local sales). Split the single GST 3% line into
   **CGST + SGST** (assume **1.5% + 1.5% = 3%** for gold, as tenant-configurable slots — confirm rate).
   Tax is a **fixed % of the total** (the taxable subtotal after discount).
2. **CGST/SGST is a per-product setting** in the catalogue (include tax: yes/no), and the **owner can
   waive it at approval** (negotiation with the customer). → new per-item field `tax_applicable`
   (default true), overridable on the quote at approval time.
3. **Per-product `needs_approval` flag** — some products' quotes go to the owner (where tax can be
   waived / negotiated), others don't. (Exact gating = **open question B**.)
4. **Labor is per-product**, set **when the owner uploads the catalogue** (product-type dependent:
   "has labor cost or not"). → new per-item labor field(s). (Mechanism = **open question A**.)
5. **Two-step reply:** the **first response is just the price**; on the customer **asking for a
   breakdown**, send the **detailed itemized quote** (karat/grams, making, CGST, SGST, labor, total),
   then negotiate (often on a call).
6. The customer estimate shows the **karat of gold** etc. (purity is already a catalog attribute).

## 4. Proposed scope (after the 2 opens are answered)

- **Catalog schema** (`verticals/jewelry/catalog/schema.json`): add per-item `tax_applicable` (bool,
  default true), `needs_approval` (bool), and labor field(s) per open-question A.
- **Pricing strategy** (`verticals/jewelry/pricing/strategy.yaml`): split `gst` → `cgst` + `sgst`
  (rate slots); add a `labor` stage (per A); make CGST/SGST conditional on the item's `tax_applicable`
  and waivable via a quote input; extend `breakdown_labels` (CGST/SGST/labor); update
  `evals/pricing_golden.yaml` goldens (tax split, tax-waived, labor, no-labor).
- **Quote card** (`verticals/jewelry/ui/templates.yaml`): CGST + SGST rows + labor row.
- **Draft (two-step):** first reply = headline price; on a breakdown request, render the **ledgered**
  itemized quote — **grounded, never invented** (§18) — and **owner-approve** per the `needs_approval`
  rule before send (customer-facing money, §19). Renderer stays **generic in `core/`** (Rule Zero);
  the pack supplies labels/format.
- **Tests:** pricing goldens (CGST/SGST split, tax waived, labor vs no-labor); a draft test proving
  the breakdown is grounded + approval-gated; the "price first, breakdown on request" branch.

## 5. Open questions (only these two block the build)

- **A — Labor mechanism.** How is a product's labor cost entered + applied? Fixed ₹ per item / per-gram
  (₹/g × net weight) / %? And is it **in addition to** the existing %-based making charge, **or** does
  it **replace** making for labor products (or is "making" already the labor, just relabeled)?
- **B — What `needs_approval` gates in the two-step flow.** Only the **detailed breakdown** (price
  auto-replies)? **Both** the price and the breakdown for a flagged product? Or **every** price?

## 6. Out of scope (unless founder expands)

- A formal **GST tax invoice** (HSN code, GSTIN, sequential invoice number) — bigger, separate ticket
  (closer to OC12 invoicing). This ticket is the **conversational estimate**.
- Auto-sending an estimate without the owner approval the `needs_approval` rule requires.
- Any `core/` change that hard-codes jewelry nouns (Rule Zero — stays in the pack).

## 7. Definition of done

Per catalog item: `tax_applicable`, `needs_approval`, and labor are configurable. A breakdown request
yields a **ledgered** itemized estimate (karat/grams × rate, making, **CGST + SGST** (waivable), labor
if any, total, validity), rendered in a **grounded, approval-gated** draft; first reply stays
price-only; goldens + draft tests pass; no `core/` industry nouns; CI green.
