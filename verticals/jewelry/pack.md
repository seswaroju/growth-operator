# Jewelry Pack (v2.0)

The extracted MVP. Everything below was formerly "the product"; it is now one installable configuration. Source of truth for pilots and Srila.

## 1. ICP & JTBD
Owner-operated stores 1–5 locations, ≥10 WhatsApp inquiries/wk; job: "when a customer DMs at night, don't lose them to the shop next door." (Full ICP scorecard: [sales playbook](../../16-sales/sales-playbook.md).)

## 2. Agent bindings
| Archetype | Persona default | Tasks | Key tool grants | Tier defaults |
|---|---|---|---|---|
| concierge | **Priya** | qualify, catalog answer, quote, book visit | catalog.search, pricing.compute(formula_weight_rate_v1), calendar.book, messages.send | replies T1, quotes ≥₹1L T2 |
| nurture | **Nisha** | reactivation nudges | messages.send, crm.read | T1, cap 3/30d |
| campaigner | **Zara** | festival campaigns | segments.query, campaigns.execute | T3 always |
| ops | **Mira** | catalog entry, rate monitoring | ingestion.review, rates.read | T1; stale-rate = fail-closed |
| support | **Asha** | repairs, orders, complaints | tickets.*, orders.read | T1; anger/legal → escalate |
| planner | — | routing, digest | bus.route | T0 |

## 3. Catalog schema (attributes)
`{purity: enum[24K,22K,18K,14K], gross_weight_g, net_weight_g, stone_details[{type,carat,price_mode}], huid: string(6), category: enum[ring,chain,necklace,bangle,earring,bracelet,coin], gender, occasion[]}` — search projection: category, purity, occasion, price band; identity key: huid|sku.

## 4. Pricing
`formula_weight_rate_v1`: net_weight × rate(purity) + making(% of metal | flat) + per-gram labor + stone value + CGST 1.5% + SGST 1.5% (per-item tax_applicable; owner may waive at approval). Rate sources: `ibja_gold`, `ibja_silver`, staleness_max 24h. Tenant slots: making %, wastage %, discount ceiling.

## 5. Workflows
`silent_lead_reactivation` (72h trigger, 3-touch cap) · `festival_campaign` (calendar-pack triggered, T3) · `visit_reminder` (T-1 day) · `post_visit_followup` (48h) · `rate_alert_hold` (stale rate → pause quoting).

## 6. Compliance
DPDP consent script (first-contact, per language) · hallmark/HUID disclosure line on quotes (BIS norms) · CGST 1.5% + SGST 1.5% on invoiceable quotes (waivable at approval) · blocked: investment-return claims about gold, purity claims without hallmark data · retention: platform defaults.

## 7. Prompt pack
Vertical layers: `concierge.jewelry.v3` (hallmark talk-track, occasion probing, never state untooled prices — inherits base money rule), `nurture.jewelry.v3`, `campaigner.jewelry.v2`, few-shots from 400 curated Srila transcripts (anonymized). Full texts: [prompt library](../../09-prompts/prompt-library.md) — being re-cut into layers per [M3](../../21-platform/migration-versioning.md).

## 8. KPIs
end-to-end handled ≥70% · first response <60s · quote provenance 100% · override <15% · visits booked/wk · attributed-₹ multiple ≥5×. Digest: chats, quotes, visits, ₹.

## 9. Integrations
go-whatsapp (WABA) · go-calendar (Google) · Razorpay payment links · IBJA rate feed (fetch_spec in pack) · Instagram catalog scrape (ingestion source).

## 10. Onboarding & UI
Wizard: WABA → profile → **rates & making %** (pack step) → catalog seed 10 items (photo-first, weight-tag extraction hints) → policies → test conversation. Quote card render template (breakdown rows: metal/making/labor/stones/CGST/SGST). Calendar pack: Indian festivals (Dhanteras, Akshaya Tritiya, wedding seasons).

---
## v3: this pack is now real files
[pack.yaml](pack.yaml) · [agents/bindings.yaml](agents/bindings.yaml) · [catalog/schema.json](catalog/schema.json) · [pricing/strategy.yaml](pricing/strategy.yaml) · workflows: [reactivation](workflows/silent_lead_reactivation.yaml), [festival](workflows/festival_campaign.yaml), [visit lifecycle](workflows/visit_lifecycle.yaml), [rate hold](workflows/rate_alert_hold.yaml) · prompts: [concierge](prompts/concierge.md), [nurture](prompts/nurture.md), [campaigner](prompts/campaigner.md), [ops](prompts/ops.md), [support](prompts/support.md) · evals: [core](evals/concierge_core.yaml), [money traps](evals/money_traps.yaml), [injection](evals/injection.yaml), [language](evals/language.yaml), [pricing goldens](evals/pricing_golden.yaml), [isolation](evals/isolation_probes.yaml) · [onboarding](onboarding/steps.yaml) · [ui](ui/templates.yaml) · [calendar](calendar/events.yaml) · integrations: [whatsapp](integrations/whatsapp.yaml), [razorpay](integrations/payments_razorpay.yaml), [calendar](integrations/calendar_google.yaml), [ibja](integrations/ibja_rates.yaml)
