# Design Review — Subscription Packaging, Entitlements, Promotions, Plan Builder
### + Public Website / Prospect → Tenant / Guided Onboarding

**Status: REVIEW ONLY — no implementation.** Produced per the founder's §75 ("BEFORE IMPLEMENTATION
— REQUIRED RESPONSE") and §76 (MVP priority). Nothing below has been built.

**Date:** 2026-08-12 · **Verification method:** read implementation + tests, not directory names
(§14). Every claim cites repo evidence.

---

# PART 1 — THE HONEST CAPABILITY AUDIT (§74)

> This is the section that should change decisions. **Six capabilities in the founder's packaging
> table are less mature than the table implies.** Two of them sit in **Recover**, the entry tier.

Maturity key: **Available** (works, tested, sellable) · **Beta** (works within stated limits) ·
**Partial** (some of it works; the sellable promise doesn't) · **Not built**.

| Capability | Repo evidence | Maturity | Sellable? | Founder's tier | Recommendation |
|---|---|---|---|---|---|
| **Ghost Lead Recovery** | `core/customers/recovery.py` + `lifecycle.py`, workflow `silent_lead_reactivation.yaml` v4, `lead_diagnoses`, 8-reason taxonomy, eval harness; GHOST-1a…1d shipped today | **Available** | **Yes** | Recover+ | ✅ As packaged |
| **AI Concierge (Priya)** | `concierge` archetype seeded, prompts, e2e `test_jewelry_journey.py` proves inquiry→catalog→quote→approve→send | **Available** | **Yes** | Recover+ | ✅ As packaged — but see *Appointment booking* |
| **Customer CRM / lifecycle** | `core/customers/` service, annotations, timeline, DPDP; `contacts`/`leads` + LEAD-1 origin model | **Available** | **Yes** | Recover+ | ✅ As packaged |
| **Catalog search & availability** | `core/catalog/` search + `availability.py`, hybrid search, embeddings (gated) | **Available** | **Yes** | Recover+ | ✅ As packaged |
| **Pricing / quote assistance** | `core/pricing/` engine + **committed-figures ledger**, jewelry itemised estimate (CGST/SGST/labour), two-step presentation | **Available** | **Yes** | Recover+ | ✅ Strongest capability in the product |
| **⚠️ Appointment booking** | `appointments` **table exists**, but **no service, no API**, and the `calendar.book` tool is an explicit **`_not_wired` stub** (`core/mediation/tools.py:164`) that raises `provider_unavailable` | **Not built** | **NO** | Recover+ (all tiers) | ❌ **Do not sell.** Either drop from all tiers or build it first |
| **Automated lead nurture** | `nurture` archetype seeded + `nurture.md` prompt + binding; **but its `crm.read` grant is a `_not_wired` stub** | **Partial** | **Carefully** | Recover+ | ⚠️ Nurture messaging works; CRM-driven nurture doesn't. Verify the promise |
| **Basic recovery insights** | `core/insights/metrics.py`, `rollup.py`, dashboard endpoints | **Available** | **Yes** | Recover+ | ✅ As packaged |
| **WhatsApp** | `core/channels/whatsapp/` — real Graph API client, **gated** (`whatsapp_live_enabled`), send gates, normalizer, templates | **Available (gated)** | **Yes** | Recover+ | ✅ Blocked only by Meta approval (BLOCKER #3), not by code |
| **Campaign Agent (Zara)** | `campaigner` archetype + bindings + tier rules + `campaigns.execute` | **Available** | **Yes** | Grow+ | ✅ As packaged |
| **WhatsApp campaigns** | `core/campaigns/send.py` — fan-out, consent/suppression re-check per recipient, approvals | **Available** | **Yes** | Grow+ | ✅ As packaged |
| **⚠️ Segmentation** | `core/campaigns/audience.py` docstring: *"The first version targets every contact with marketing consent … Segment-targeting (`segments.definition` → contacts) is **a fast follow-up**"* | **Not built** | **NO** | Grow+ | ❌ **Do not sell as "Segmentation."** Today it's "all consented customers" |
| **Campaign analytics / attribution** | `core/campaigns/attribution.py`, `attributions`, `campaign_touches`, funnel + first-touch | **Available** | **Yes** | Grow+ | ✅ As packaged |
| **Landing-page generation** | LP-1…LP-4b: deterministic renderer, 5 layouts, gated LLM planner, lifecycle, approval | **Available** | **Yes** | Grow+ | ✅ As packaged |
| **Landing lead capture** | LP-3b: consent-gated capture → contacts/leads + parked concierge draft | **Available** | **Yes** | Grow+ | ✅ As packaged |
| **Instagram publishing** | `core/channels/instagram/` — real Graph two-step, **simulated by default**, needs `instagram_live_enabled` + Meta access | **Beta (gated)** | **Carefully** | Grow+ | ⚠️ Label **Beta** until Meta access exists |
| **Advanced analytics / reports** | `core/insights/`: `metrics`, `reports`, `churn`, `agents`, `rollup` — real but modest (≈3 functions each) | **Partial** | **Carefully** | Grow "Limited" / Scale ✓ | ⚠️ Define `analytics.standard` vs `analytics.advanced` against what exists (see §23 answer) |
| **Ops Agent (Mira)** | `ops` archetype seeded with `ingestion.review`, `catalog.write`, `rates.read` | **Available** | **Yes** | Scale | ✅ As packaged |
| **Automated catalog ingestion** | `core/ingestion/` — batches, stage transitions, caps, review | **Available** | **Yes** | Scale | ✅ As packaged |
| **Gold / rate operations** | `core/pricing/rates.py` — IBJA fetcher + bounds + manual entry; **gated** (`rates_provider_enabled`) | **Available (gated)** | **Yes** | Scale | ✅ But it is an **L1 jewelry** capability (see §55 answer) |
| **⚠️ Support Agent (Asha)** | `core/support/` tickets exist. **But `support` is NOT a seeded archetype** — `core/packs/archetypes.py` states no level-1 allowlist is defined for it | **Partial** | **NO (as an agent)** | Scale | ❌ Sell "Support tickets", **not** "Support Agent", until the archetype exists |
| **Google Ads** | `core/channels/google_ads/` — real REST two-step, **campaign created PAUSED**, gated `google_ads_live_enabled` | **Beta** | **Carefully** | Scale "Beta → ✓" | ✅ Founder's Beta label is **exactly right** |
| **Competitor watchlist** | `core/competitors/` — CRUD (create/list/get/delete) | **Partial** | **Carefully** | Scale | ⚠️ It's a *list*, not monitoring/intelligence. Sell as "Competitor list" |
| **SEO / AEO / GEO Agent** | **Nothing** | **Not built** | **NO** | "Planned — don't sell" | ✅ Founder's call is **correct** |
| **Staff users (2/5/10)** | `max_managers`/`max_staff` + `check_seat` enforced at invite (CP-3) | **Available** | **Yes** | 2 / 5 / 10 | ✅ Already enforced |

## The six flags, ranked by commercial risk

1. **Appointment booking — in ALL THREE tiers, not built.** A table with no service and an unwired
   tool. This is the most exposed claim: it's in the entry tier a pilot store pays ₹3,999 for.
   **Options:** (a) remove from the packaging table, (b) build a minimal booking flow first,
   (c) relabel as "visit requests handled by the concierge" if that's what actually happens.
2. **Segmentation — in Grow, not built.** The code says explicitly it's a follow-up. Grow currently
   delivers "campaign to all consented customers".
3. **Support Agent — in Scale, archetype not seeded.** Ticket handling exists; the *agent* doesn't.
4. **Competitor watchlist — in Scale, CRUD only.** No monitoring or intelligence.
5. **Advanced analytics — "Limited"/✓.** Needs a concrete `standard` vs `advanced` split defined
   against what `core/insights` really does, rather than a marketing word.
6. **Automated lead nurture — its CRM tool is a stub.** Messaging works; CRM-driven nurture doesn't.

**Nothing here changes your pricing or packaging intent (§C: "do NOT silently change pricing/package
intent"). These are flags for your decision.**

---

# PART 2 — §75 REQUIRED RESPONSE

## A. Existing architecture to reuse (Already Exists / Should Extend / Should Not Duplicate)

| Area | Status | Component |
|---|---|---|
| Billing plans + subscriptions | **Already exists → extend** | `billing_plans` (id, name, price_minor, active, description, **features**, max_managers, max_staff, **config**), `billing_subscriptions` (org_id, plan_id, status, started_at, cancelled_at), `core/billing/service.py`, operator API |
| Plan UI | **Partially exists → replace the editor** | web-ops `FinancialSection` — free-text/comma-separated |
| **Entitlements** | **Partially exists (ENT-1a, shipped today) → extend** | `core/tenancy/entitlements.py`: catalog + baseline/grantable + `entitlements(org)` + `requires_feature()` + RFC7807 403. **This is the seed of PLAN-1/2 — extend, don't replace** |
| Seats | **Already exists** | `core/tenancy/seats.py::check_seat` (409 at cap) |
| Agents | **Already exists** | `agent_archetypes.capability_allowlist` (migration 008 + 047), `verticals/jewelry/agents/bindings.yaml`, `activate_plan_agents` (CP-2b) |
| Channel registry | **Already exists → use as source** | `core/channels/registry.py` (`CHANNEL_TYPES`) — §20's requirement is satisfiable directly |
| Announcements | **Already exists → extend** | `announcements` (global, no RLS), `core/notifications/admin.py` + `service.get_feed` |
| Media security | **Already exists → extract** | `core/channels/whatsapp/media.py`: `ALLOWED_MIME`, `MAX_MEDIA_BYTES`, `MediaScanner` (fail-closed), `MediaStore`, simulated defaults. **LP-4b already reuses it** — proof the abstraction travels |
| Tenant creation | **Already exists → reuse for conversion** | `core/tenancy/provisioning.py::provision_store` + `finalize_store_setup` (org + owner + subscription + pack install + agent activation, atomic-then-finalize) |
| Onboarding checklist | **Partially exists** | `insights.service.onboarding_status` (OC11) — owner-facing, derived from data |
| Operator plane | **Already exists** | `require_platform`, `platform.tenants:*`, `log_platform_access`, SECDEF cross-tenant reads |
| Audit / RLS | **Already exists** | hash-chained `audit_log`, FORCE RLS, `set_org_context` |

**Should NOT duplicate:** billing, subscriptions, media storage, announcements, tenant creation,
auth/invites, CRM. (§77.22)

## B. Capability inventory → see Part 1 table (key/label/category/kind/status/source/sellable)

Proposed **kinds** (§13): `feature · agent · channel · channel_capability · addon · limit`.
Proposed **categories** (§19): Lead Conversion · AI Agents · Customer Communication · Campaigns ·
Paid Growth · Conversion · Analytics · Operations · Support · Organic Growth.

## C. Recover/Grow/Scale reconciliation → Part 1, with the six flags

## D. Persistence recommendation

**Use `billing_plans.config` JSONB now; do not normalize yet.** Reasons: the column exists and is
already jsonb; entitlements are read-mostly and always resolved as a whole set (no relational
queries needed); a promotion list nests naturally; and §76 says don't build a billing platform.
Normalize later **only if** you need cross-plan queries ("which plans include Campaigns?") or
per-tenant overrides at scale. `features` (list[str]) stays for **display/legacy**; machine
entitlements live in `config.entitlements` — this directly satisfies §32 (machine ≠ marketing copy).

## E. Default plan representation (no live inheritance, §16/§39)

```jsonc
// billing_plans.config for "Grow" — an explicit SNAPSHOT copied from Recover, then extended
{
  "entitlements": [
    {"key": "ghost_lead_recovery", "kind": "feature",  "enabled": true, "source": "plan"},
    {"key": "concierge",           "kind": "agent",    "enabled": true, "source": "plan"},
    {"key": "whatsapp",            "kind": "channel",  "enabled": true, "source": "plan"},
    {"key": "campaigns",           "kind": "feature",  "enabled": true, "source": "plan"},
    {"key": "landing_pages",       "kind": "feature",  "enabled": true, "source": "plan"},
    {"key": "campaigns",           "kind": "feature",  "enabled": true, "source": "promotion",
     "starts_at": "2026-09-01T00:00:00Z", "ends_at": "2026-10-01T00:00:00Z",
     "promotion_label": "30-day Grow trial"}
  ],
  "limits":   {"staff": 5},
  "commercial": {"positioning_label": "Most Popular", "recommended": true,
                 "tagline": "Generate and convert more demand.",
                 "public_visibility": true, "public_slug": "grow", "display_order": 2}
}
```

## F. Entitlement resolver (§34)

Extend `core/tenancy/entitlements.py`:
`effective_entitlements(org_id)` = **baseline** ∪ **plan entitlements** ∪ **active promotions**
(→ future: ∪ tenant overrides). One function; `requires_feature()` already routes through it, so no
`if plan.config[...]` spreads anywhere (§34). Belongs in `core/tenancy/` (L0) beside seats.

## G. Enforcement rollout (§35)

**Gate now:** `landing_pages` ✅ (done), `campaigns.whatsapp` ✅ (done), plus `instagram_publishing`,
`google_ads`, `catalog_ingestion`, `competitor_list`, `analytics.advanced` — enough to prove the
model. **Follow-up (explicitly ungated, disclosed):** agent activation paths, rate operations,
support tickets, per-channel capability checks. **I will not claim complete enforcement.**

## H. Promotion semantics (§29) — CONFIRMED

`enabled AND starts_at <= now AND (ends_at IS NULL OR now < ends_at)`, **UTC**, **start inclusive /
end exclusive**, computed at read time. **No cron.** This matches the pattern already shipped in
GHOST-1b/1c (expired snooze falls through with no cleanup job) — repository-consistent.

## I. Announcement attachments (§45/§46)

**Extract** the generic parts of `core/channels/whatsapp/media.py` → **`core/media/`** (scanner
protocol, store protocol, MIME/size policy, simulated defaults), leaving WhatsApp-specific ingest
behind. `core/notifications` then imports `core/media` — **no notifications→WhatsApp coupling**.
LP-4b (`core/landing/assets.py`) currently imports the WhatsApp module and would move to
`core/media` in the same change — a second consumer proves the extraction is right, not speculative.

## J. UI changes

web-ops: **Plans** list (Recover/Grow/Scale cards, positioning badges) → **Plan Builder** (grouped +
searchable capability selector, start-from-plan copy, staff limit, promotions editor, live
**Plan Preview** in business language per §18/§37, subscriber-impact confirmation per §36) ·
**Announcement composer** with attachments · **Prospects** table + detail + Convert-to-Tenant.
web: owner nav/section gating from `/v1/me` features; onboarding progress.
**New public site:** separate surface (see Part 3).

## K. Files/modules affected → listed per ticket in Part 4

## L. Backward compatibility (§33)

Compatibility loader: existing `features` (display strings) + `config.agents` / `config.channels` /
`config.addons` → canonical entitlements where they map; **anything unmappable is preserved as
`legacy_display`**, never discarded. ENT-1a's baseline already guarantees existing stores keep
working (every plan's `features` is `[]` today).

## M. Tests (§60–66) → per-ticket in Part 4; includes the §63 UTC boundary matrix
(before/at-start/mid/at-end) and §62 copy-independence.

## N. Ticket slicing → Part 4.

---

# PART 3 — PUBLIC WEBSITE / PROSPECT / ONBOARDING (design review)

**Received §1–63** (truncated at ~§63 — anything after is still missing).

**Gap analysis:** the public marketing site **does not exist** (Missing). `web/` is the authenticated
owner app; `web-ops/` the operator plane; `/p/{page_id}` serves *merchant→shopper* campaign pages.
The founder's four-surface separation (§2) is **architecturally sound and already half-true** — the
only new surface is the marketing site.

**Key correctness points I'd carry into design:**
- **§32 is critical and I agree:** prospects must **not** live in `core/customers` (that's
  tenant→shopper). A separate `core/prospects` (platform sales) keeps the domains clean — the same
  discipline that made LEAD-1 work.
- **§7 sanitized public projection:** the public API must return a `PublicPlan` view, never the
  internal `config`. This is a real leak risk worth designing deliberately.
- **§38–40 conversion:** reuse `provision_store`/`finalize_store_setup`; store
  `prospect.converted_org_id`; idempotent (double-click safe) — the existing provisioning is already
  atomic-then-finalize, which fits.
- **§13 no self-service signup** — matches the current operator-provisioned model exactly.
- **§4 rendering:** the marketing site wants SEO/static; the owner app is a React SPA. A separate
  static-generated surface is justified — **but** it's the single biggest scope item here, and §76
  says don't overbuild. Recommend deferring the site build until the entitlement work lands, since
  §7 makes the pricing page *depend* on the public plan projection.

---

# PART 4 — PROPOSED TICKETS (smallest coherent slices)

| Ticket | Scope | Migration? |
|---|---|---|
| **PLAN-1** | Capability catalog: kinds, statuses, categories, dependencies, L1 vertical contributions (gold/rate via the pack, §55). Extends ENT-1a's catalog | No |
| **PLAN-2** | Structured entitlements in `config.entitlements` + compatibility loader + `effective_entitlements` incl. **promotions** (time-derived) | No |
| **PLAN-3** | Recover/Grow/Scale presets seeded from the catalog + copy-from-plan (snapshot, no inheritance) | Data-only |
| **PLAN-4** | Operator **Plan Builder** UI: grouped/searchable selector, promotions editor, plan preview, subscriber-impact confirmation, audit detail | No |
| **PLAN-5** | Extend enforcement to the listed capabilities + publish the ungated list | No |
| **ANNOUNCE-1** | Extract `core/media/` (WhatsApp + landing move onto it) — pure refactor, no behaviour change | No |
| **ANNOUNCE-2** | Announcement attachments: metadata table, upload (AV fail-closed), operator composer | **Yes** |
| **ANNOUNCE-3** | Owner rendering + authenticated download (never expose `s3://`) | No |
| **WEB-1** | `core/prospects` + public Growth-Review submission endpoint (rate-limited, consent, UTM) | **Yes** |
| **WEB-2** | Operator Prospects UI + notes/status | No |
| **WEB-3** | Convert-to-Tenant (idempotent, reuses provisioning) | Small |
| **WEB-4** | Public plan projection API (`PublicPlan`, sanitized) | No |
| **WEB-5** | Marketing site (static) — hero/problem/tiers/pricing/trust/Growth-Review/login | No (new surface) |
| **WEB-6** | Guided onboarding (declarative steps, entitlement-aware) | Likely |

**Recommended order for the MVP demo (§76):** PLAN-1 → PLAN-2 → PLAN-3 → PLAN-4 → PLAN-5, which
delivers exactly the founder's presentation-critical flow (*operator opens Plans → sees the three
tiers → edits → picks real capabilities → adds a promo → saves → assigns to a store → GO knows the
entitlements*). ANNOUNCE-1/2/3 next (small, self-contained). WEB-\* after.

---

# OPEN QUESTIONS FOR THE FOUNDER

1. **Appointment booking** — in all three tiers but not built. Drop it, build it, or relabel it?
2. **Segmentation** — in Grow but not built ("all consented customers" today). Drop from Grow, or
   build it as part of the Grow promise?
3. **Support Agent** — sell as "Support tickets" (real) rather than "Support Agent" (archetype not
   seeded)?
4. **Competitor watchlist** — sell as "Competitor list" (CRUD is what exists)?
5. **Advanced analytics** — what concretely separates `standard` from `advanced`? Proposal:
   standard = recovery + campaign dashboards; advanced = churn insights, agent analytics,
   cross-period reports (all of which exist in `core/insights`).
6. **Activation fee** (₹2,999, website §17) — model as plan metadata now, or defer?
7. **Part 2 §64+** is still missing (truncated).
