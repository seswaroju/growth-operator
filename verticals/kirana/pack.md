# Kirana Pack (v0.1 — M7 acceptance-test pack)

Purpose: prove second-vertical installation touches zero core code. Deliberately simple.

| Module | Config |
|---|---|
| ICP/JTBD | Neighborhood grocery, WhatsApp order lists ("send 2kg atta, 1 Maggi pack"); job: capture list-orders + reorder nudges without owner typing all day |
| Agents | concierge **"Anu"**: parse order lists → order draft (T1), confirm total (T2 if >₹2,000); nurture: weekly-staples reorder nudge (cap 1/wk); ops: stock/price sheet ingestion; no campaigner in v0.1 |
| Catalog | `{brand, pack_size, unit: enum[kg,g,l,ml,pc], mrp, veg_flag?}`; identity: barcode|brand+size; availability: in_stock w/ low-stock threshold |
| Pricing | `static_list_v1`: MRP − offer overlays (slab discounts); no rate sources; tax: GST per HSN category table (pack-provided defaults) |
| Workflows | `order_intake` (list msg → parsed draft → owner-visible → confirm → payment link) · `reorder_nudge` (30d staple cycle) · `low_stock_alert` (internal) |
| Compliance | DPDP consent; blocked: medicines/liquor line-items (flag to owner, never auto-confirm — licensing) |
| Prompts | concierge.kirana.v1: multilingual list parsing few-shots (Hinglish "aata"≡atta), quantity disambiguation, substitution etiquette |
| KPIs | orders auto-parsed % ≥80, parse-error rate, repeat-order rate, basket size |
| Integrations | go-whatsapp, Razorpay links; optional: simple POS CSV ingestion |
| Onboarding | Wizard: WABA → profile → **price-sheet photo/CSV upload** (pack step) → top-50 staples confirm → test order |

Authoring budget actuals to be recorded here — target ≤3 wk, core diffs = 0.

---
## v3: this pack is now real files
[pack.yaml](pack.yaml) · [agents/bindings.yaml](agents/bindings.yaml) · [catalog/schema.json](catalog/schema.json) · [pricing/strategy.yaml](pricing/strategy.yaml) · workflows: [order intake](workflows/order_intake.yaml), [reorder nudge](workflows/reorder_nudge.yaml) · prompts: [concierge](prompts/concierge.md), [nurture](prompts/nurture.md), [ops](prompts/ops.md) · evals: [order parse](evals/order_parse.yaml), [pricing goldens](evals/pricing_golden.yaml) · [onboarding](onboarding/steps.yaml) · [ui](ui/templates.yaml) · integrations: [whatsapp](integrations/whatsapp.yaml), [razorpay](integrations/payments_razorpay.yaml)
