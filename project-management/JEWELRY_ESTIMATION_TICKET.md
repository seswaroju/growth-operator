# Ticket JWL-EST-01 — Jewelry-style customer estimation (itemized), not a flat price

**Status:** Proposed — awaiting founder approval (raised 2026-08-10 during OC7; parked until the OC5–OC12 run reaches a stopping point).
**Track:** Jewelry vertical pack (L1) + customer-facing draft. **Not** operator-console (OC).

---

## 1. Founder request (verbatim intent)

> "When a customer asks about an item, create a jewelry-type **estimation** rather than just a price.
> They expect **grams of gold, making charges, CGST, SGST, labor charges if any**, etc."

## 2. What already exists (important — scope is narrower than it looks)

The pricing **engine** (`core/pricing/engine.py`) already runs a strategy's ordered stages over exact
arithmetic and returns an **itemized breakdown + total** (each stage is a breakdown line, ledgered).
The **jewelry strategy** (`verticals/jewelry/pricing/strategy.yaml`) already computes, per item:

`metal_value` (net_weight_g × live rate/g for the purity) → `wastage` → `making` (making charges,
%-with-minimum) → `stones` → `subtotal` → `discount` (tier-2 if nonzero) → `gst` (3%) → `total`,
with human `breakdown_labels` ("{purity} {metal} · {net_weight_g}g × ₹{rate}/g", "Making charges",
"GST (3%)"). Rates come from a staleness-bounded source (`stale_rate` guard); figures are ledgered
(`unledgered_figure` guard). **So the itemized estimation math is built.**

## 3. The actual gaps

1. **Tax split — CGST + SGST (and IGST).** The strategy emits a single **GST 3%** line. Indian jewelry
   invoices show **CGST 1.5% + SGST 1.5%** for intra-state and **IGST 3%** for inter-state. The founder
   explicitly wants CGST/SGST shown separately. → Split the `gst` stage into `cgst`/`sgst` (intra) or
   `igst` (inter), chosen by the store's state vs the customer's state (place-of-supply). **Declarative
   pack change** (tax_rules + stages + labels); no `core/` change.
2. **"Labor charges if any".** "Making charges" is usually the labor. If a store itemizes labor
   separately from making, add an optional `labor` stage/line (pack config, slot-driven). Confirm with
   founder whether making == labor or they're distinct.
3. **Surface the breakdown to the customer (the real complaint).** The customer-facing **AI draft**
   appears to state a price, not the itemized estimation. → The draft for a price/quote inquiry must
   **render the ledgered breakdown** (grams, rate, making, CGST, SGST, total, validity) from the
   computed quote — **grounded, never invented** (§18) — and remain **approval-gated** (customer-facing
   money, §19). The renderer must stay **generic in `core/`** (Rule Zero); the jewelry pack supplies the
   labels/format.

## 4. Proposed scope

- **L1 pack (`verticals/jewelry/`):** split GST → CGST/SGST/IGST by place-of-supply; optional `labor`
  line; extend `breakdown_labels`; update `evals/pricing_golden.yaml` golden cases.
- **Draft grounding:** compute the quote (`core/pricing/service.compute_quote`) for a detected item +
  weight/purity, attach the ledgered breakdown as evidence, and render an itemized estimate in the
  draft (generic renderer + pack labels). Record prompt/model/evidence (§18).
- **Approval + audit:** the estimate is a customer-facing draft → human approval before send; figures
  ledgered; quote validity + `rate.updated` invalidation already defined in the strategy.
- **Tests:** golden pricing cases for the CGST/SGST split (intra vs inter-state) and labor; a draft
  test proving the breakdown is grounded (no invented grams/rates) and approval-gated.

## 5. Open questions for the founder

- **Place of supply:** how do we know intra- vs inter-state? (store state slot + customer state — is
  customer state captured, or default to intra-state CGST/SGST?)
- **Making vs labor:** one line ("making charges") or two ("making" + separate "labor")?
- **Weight/purity source:** does the customer state grams/purity, or is it read from the catalog item?
  (an estimate needs net_weight_g + purity — from the catalog item, the customer, or asked back?)
- **HSN / invoice-grade fields:** is this a *conversational estimate* only, or must it match a
  GST-invoice format (HSN code, GSTIN)? (The latter is a bigger, separate invoicing ticket.)

## 6. Out of scope (unless founder expands)

- A formal GST tax invoice (HSN, GSTIN, sequential invoice no.) — that's closer to OC12/invoicing.
- Auto-sending the estimate without human approval.
- Any `core/` change that hard-codes jewelry nouns (Rule Zero — must stay in the pack).

## 7. Definition of done

Itemized estimate (metal value with grams×rate, making, **CGST+SGST / IGST**, optional labor, total,
validity) is produced from the **ledgered** quote, rendered in a **grounded, approval-gated** draft;
golden + draft tests pass; no `core/` industry nouns; CI green.
