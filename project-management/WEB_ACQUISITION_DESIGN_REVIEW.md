# Design Review — Public Website, Sales Prospects, Tenant Conversion, Guided Onboarding

**Status: REVIEW ONLY — no implementation.** Per §115 ("BEFORE IMPLEMENTATION — REQUIRED RESPONSE")
and §35/§114 (MVP priority). Companion to `PLAN_ENTITLEMENTS_DESIGN_REVIEW.md`; the two are
reconciled, not separate projects (§ preamble).

**Date:** 2026-08-12 · **Method:** audited the repository per §98/§99/§100 before proposing anything.

---

## A. CURRENT SURFACE MAP (what exists today)

| Surface | Exists? | Reality |
|---|---|---|
| **Owner app** | ✅ | `web/` — React 19 SPA, authenticated. Routes: Home, Approvals, Conversations, Catalog, Customers, Campaigns, **Landing pages**, Automations, Insights, Support, Team, Settings. Nav gated by **role permissions** + (ENT-1a) plan features via `/v1/me` |
| **Operator console** | ✅ | `web-ops/` — Stores, Financial/Plans, Channels, Models, Cost & margin, Invoices, Budgets, Announcements, Queue, Analytics, **Leads roster** (CP-8). Behind `require_platform` + admin-plane flag |
| **Merchant landing pages** | ✅ | `GET /p/{page_id}` — public, published-only, `noindex`, rate-limited (LP-3a) |
| **Public marketing website** | ❌ **MISSING** | Nothing. The only public surfaces are the ones below |
| **Auth / login** | ✅ | Passwordless OTP (`auth_router`), sessions + refresh rotation. **One auth surface**; operator access is a separate `platform_admins` grant, not a second login system |
| **Other unauthenticated routes** | ✅ (deliberate) | `health_router`; WhatsApp ingress webhook (signature-verified); Razorpay webhook; `POST /v1/landing/track`; `POST /p/{id}/lead`. **All narrow and intentional** |
| **Tenant creation** | ✅ | `provisioning.provision_store` → org + owner + membership + **active subscription**, plan validated *before* any write (atomic); then `finalize_store_setup` → pack install + `activate_plan_agents` |
| **Onboarding** | ⚠️ Partial | `insights.service.onboarding_status` (OC11) — owner-facing, **derived** (whatsapp_connected, catalog_items, campaigns, team_members). Four signals, not the §45 twelve-step model. **No operator onboarding view** |
| **Prospects / sales pipeline** | ❌ **MISSING** | Nothing at all |

## B. GAP ANALYSIS

| Requirement | Verdict |
|---|---|
| Public marketing site | **Missing** — new surface |
| Public plan projection (§7) | **Missing** — must be built; `billing_plans` exists but is internal-only |
| Growth Review intake | **Missing** |
| Platform prospect model | **Missing** |
| Operator prospect UI | **Missing** |
| Convert-to-Tenant | **Should Extend** — `provision_store`/`finalize_store_setup` already do the work; compose, don't duplicate (§12/§98) |
| Owner invitation | **Already Exists** — OTP auth + `invites_router`; **do not invent a second mechanism** (§44) |
| Vertical install | **Already Exists** — pack installer, invoked by `finalize_store_setup` |
| Subscription assignment | **Already Exists** — `provision_store` takes `plan_id` and writes an active subscription |
| Promotions at conversion (§89) | **Should Extend** — use PLAN-4's promotion system, never a `sales_trial_features` second system |
| Guided onboarding | **Partially Exists → extend** `onboarding_status` into a declarative, entitlement-aware model (§47) |
| Operator notification of new prospect (§87) | **Should Extend** — the operator bell/feed pattern exists (`web-ops` OperatorBell) |
| Rate limiting for a public form | **Already Exists → reuse** `core/landing/ratelimit.py` (in-process sliding window, built for LP-3a) |
| Media/attachments | **Already Exists → extract** (see the entitlements review, ANNOUNCE-1) |
| Audit for operator actions | **Already Exists** — `log_platform_access` + hash-chained `audit_log` |

## C. PROPOSED SURFACE ARCHITECTURE (§2)

```
www.<domain>    →  NEW: static marketing site      (public, indexable)
app.<domain>    →  web/        (owner app)          (authenticated, noindex)
ops.<domain>    →  web-ops/    (operator console)   (authenticated + admin-plane flag)
pages.<domain>  →  GET /p/{id} (merchant campaign)  (public, NOINDEX — §101)
```

**§101 is a real trap worth calling out:** merchant campaign pages deliberately send
`X-Robots-Tag: noindex` (LP-3a). The marketing site must be **indexable**. These must never share one
robots policy — they are different surfaces with opposite SEO intent.

**Backend stays one FastAPI app.** The marketing site is a separate *frontend* deployment consuming
two deliberately public endpoints (§76). That satisfies §102 (deploy content without touching the
authenticated app) without microservice complexity.

## D. PUBLIC PLAN PROJECTION (§7) — the sanitized contract

```jsonc
// GET /public/plans  → PublicPlan[]  (unauthenticated, cacheable)
{
  "slug": "grow",
  "name": "Grow",
  "tagline": "Generate and convert more demand.",
  "price_display": "₹6,999",
  "billing_interval": "month",
  "positioning_label": "Most Popular",
  "recommended": true,
  "display_order": 2,
  "public_features": [                      // LABELS + status, never machine keys
    {"label": "Ghost Lead Recovery", "status": "available"},
    {"label": "WhatsApp Campaigns",  "status": "available"},
    {"label": "Instagram publishing","status": "beta"}
  ],
  "public_limits": [{"label": "Staff users", "value": "5"}],
  "external_fee_disclosures": ["Meta messaging fees billed separately",
                               "Advertising spend billed separately"]
}
```

**Never exposed:** `config`, machine entitlement keys, promotions, subscriber counts, internal beta
flags, agent ids, audit data. Derived by an explicit **allow-list projection**, not by deleting
fields from the internal record — so a new internal field can never leak by default (§7, §77).
**Only plans with `commercial.public_visibility = true`** appear (§8/§94), so Founder Pilot /
Association Special / Store XYZ Custom stay private. **`status: planned` never renders as included
value** (§9/§72).

## E–F. WEBSITE IA + HOMEPAGE (§10, §11, §14, §15, §71)

**MVP routes:** `/` · `/pricing` · `/trust` · `/book-growth-review` · `/privacy` · `/terms` · Login
(external link). Product/How-it-works/For-jewelers start as **homepage sections** (§10 explicitly
allows this) — fewer pages, better MVP.
**Deferred:** blog, academy, docs portal, community, careers, partner marketplace (§10).

**Homepage order** (business first, architecture never — §5):
1. **Hero** — "Turn missed jewelry inquiries into revenue. Then grow from there." · CTA **Book a
   Growth Review** · secondary **See Plans** · tertiary Login / WhatsApp
2. **Problem** — the revenue-loss journey (quote → silence → busy staff → nobody follows up → lost)
3. **The fix** — the same journey with GO, owner keeping control
4. **Recover / Grow / Scale** with prices, **Grow visually emphasised** (§95, no dark patterns)
5. **The AI team** — Priya/Nisha/Zara/Mira/Asha as *capabilities of one operator* (§19/§20)
6. **Closed loop** — channels → customer → CRM → follow-up → campaign → sale → attribution → insight
7. **Trust** — in owner language ("AI doesn't invent your prices"), not RLS/CEL/ed25519 (§23)
8. **Pricing summary** → full table · 9. **Growth Review form** · 10. **Footer** (§64)

**No fake social proof anywhere** (§25) — no logos, testimonials, counts, or case studies until real.

## G. PROSPECT DOMAIN MODEL (§32) — the boundary that matters most

**`core/prospects` (NEW, L0 platform) — NOT `core/customers`.** Two different CRMs:

```
Growth Operator → prospective jewelry stores   =  core/prospects   (platform sales)
Lavanya Jewellers → her shoppers               =  core/customers   (tenant CRM, RLS)
```

**This is the same discipline that made LEAD-1 correct**, and it has a concrete security
consequence: prospect rows are **platform-owned, NOT org-owned**, so they must **not** carry
`org_id` or tenant RLS (§66). They live behind the operator plane like `platform_admins` /
`announcements` do today. Getting this wrong would either expose GO's sales pipeline to tenants or
mis-scope it into nothing.

Fields per §33 (+ `converted_org_id` per §39, `consent_to_contact_at` per §29).

## H. LIFECYCLE (§34)

`new → contacted → demo_booked → qualified → plan_selected → won → onboarding` (+ terminal
`not_now | lost | disqualified`). **`active` is NOT a prospect state** — §34 asks and the answer is:
once converted, the *organization* is the source of truth (§40). Keeping `active` on the prospect
would create exactly the contradictory lifecycle §34 warns about.

## I. GROWTH REVIEW API (§65, §106)

`POST /public/prospects` — **creates a prospect and nothing else**. It must not create an org,
subscription, plan, pack, login, channel, campaign or message (§65). Validation: required fields,
normalized phone, length caps, **explicit consent**, HTML/script sanitization, honeypot, per-IP rate
limit (reuse `core/landing/ratelimit.py`), duplicate flagging by normalized phone within a window
(§70 — **flag, never overwrite** sales history). UTM/referrer/landing_path captured as **platform
acquisition attribution** — deliberately *not* `attributions` (§31/§92: platform CAC ≠ tenant
campaign ROI). Response is neutral `{ok:true}` — no existence leaks. **No PII in logs/telemetry.**

## J. OPERATOR PROSPECT UI (§36)

Table: Store · Owner · City · WhatsApp · Source · Interested Plan · Status · Created.
Detail: contact, source, stated pain, volume band, interested plan, notes, timeline, **Convert to
Tenant**. Follows existing web-ops card/table patterns. "New prospects: 3" in the console (§87) —
no paging system.

## K. CONVERT TO TENANT — reused primitives (§38, §41, §98)

| Step | Existing primitive |
|---|---|
| Create org + owner + membership + subscription | **`provisioning.provision_store`** (already atomic, plan validated first) |
| Install vertical + activate plan agents | **`provisioning.finalize_store_setup`** |
| Apply promotions | **PLAN-4 promotion system** (never a second trial system, §89) |
| Owner access | **existing OTP auth + invites** (§44 — no second auth, no emailed passwords) |
| Audit | **`log_platform_access`** + `audit_log` |

**Idempotency (§39):** `prospect.converted_org_id` set inside the same transaction that provisions;
a prospect already carrying one refuses re-conversion and shows "Converted → <org>". Double-click
safe. **Permission (§67):** viewing prospects = a read permission; **converting** = a higher
`platform.tenants:manage`-class permission, since it creates a tenant and assigns a paid plan.

## L. ONBOARDING RECONCILIATION (§45–§50, §99)

**Already exists:** `onboarding_status` — 4 derived signals, owner-facing.
**Recommended extension:** a **declarative step catalog** whose *applicable* steps are computed from
`vertical + effective_entitlements + current configuration` (§47) — so a custom plan needs no bespoke
onboarding flow, and **no step appears for a capability the tenant isn't entitled to** (§46).
**Derive readiness, don't tick boxes** (§50): org active, plan active, pack installed, required
channel connected, required data present. **"Tenant exists" ≠ "production-ready"** — and per §51,
conversion must **not** flip any live-provider flag; simulation/approval gates stay authoritative.

## M. SECURITY REVIEW (§78, §107)

- **New public attack surface:** exactly one write endpoint. Narrow, validated, rate-limited,
  consent-gated, honeypot, size-capped, sanitized. Documented as hostile-input territory.
- **Platform/tenant boundary:** prospects are platform-owned; tenant users must never list them
  (§66). Tested explicitly.
- **PII:** prospect contact data — consent recorded, purpose-limited, deletable/anonymizable (§69),
  never in logs or analytics events (§29/§53).
- **No internal config through public APIs** (§77) — allow-list projection only.
- **Marketing site:** CSP, no inline script where avoidable, no secrets in the bundle, no operator
  APIs reachable, safe redirects (§78).
- **Conversion ≠ bypass:** RLS, approvals, execution tokens, consent, provider gates all remain
  (§52).

## N. SEO / AEO / GEO (§55, §56)

Static generation, semantic HTML, canonical URLs, title/meta, OpenGraph, sitemap, `robots.txt`
(**index** — opposite of `/p/{id}`), JSON-LD `Organization` + `Product`/`Offer` from the *public plan
projection* (so structured data can't drift from pricing), FAQ block for answer engines. No keyword
stuffing, no programmatic page farms.

**Rendering choice (§4):** **static generation** (Astro or Vite SSG) for the marketing site — the
owner app being a React SPA does not imply the same strategy. Trade-off: a second frontend toolchain
vs. an SPA that is bad at SEO and first paint. Given §55 (indexable) + §82 (mobile-first, opened from
a WhatsApp link) + §102 (deploy independently), static wins clearly. Reuse the design tokens from
`web/` so it looks like one company.

## O. MODULES AFFECTED

New: `core/prospects/` (model, service, public API, operator API) · `web-public/` (new frontend) ·
`web-ops` Prospects section · `core/billing/public.py` (projection).
Extended: `core/tenancy/provisioning.py` (conversion composition), onboarding service,
`core/common/config.py` (`marketing_base_url`, `owner_app_base_url`, `operator_app_base_url`,
`landing_pages_base_url` — §63, not hard-coded).

## P. MIGRATIONS

`prospects` (+ `prospect_notes` or a jsonb activity trail) — **platform-owned, no RLS/org_id**;
onboarding step state if it can't be fully derived.

## Q. TEST PLAN (§105–§111)

Public plan projection (only public plans; internal config never exposed; Beta preserved; planned
never shown as included) · prospect form (valid, required, bad phone, oversize, script payload, rate
limit, consent, UTM, duplicates, **and that no tenant/subscription/login/message is created**) ·
prospect security (tenant owner cannot list; unauthorized operator cannot manage; conversion needs
the higher permission) · lifecycle transitions · **conversion idempotency** (second attempt refused,
no duplicate org) · onboarding (plan-aware steps; Recover doesn't show Scale-only steps) · marketing
site (renders unauthenticated, owner app still protected, metadata/sitemap/robots, mobile, a11y, no
secrets in bundle).

## R. TICKETS

| Ticket | Scope | Migration |
|---|---|---|
| **WEB-1** | Public plan projection API + `commercial` metadata on plans | No |
| **WEB-2** | `core/prospects` + `POST /public/prospects` (validation, consent, rate limit, UTM) | **Yes** |
| **WEB-3** | Operator Prospects UI (table, detail, notes, status) + "new prospects" count | No |
| **WEB-4** | Convert-to-Tenant (idempotent, composes existing provisioning, audited) | Small |
| **WEB-5** | Marketing site foundation (static, tokens, layout, SEO/robots/sitemap, config-driven URLs) | No |
| **WEB-6** | Home + Pricing + Trust content + Growth Review form wired to WEB-2 | No |
| **ONBOARD-1** | Declarative, entitlement-aware onboarding + operator readiness view | Likely |

**Dependency:** WEB-1 needs PLAN-1/PLAN-2 (the capability catalog + `commercial` metadata) — which is
why the entitlement work should land first.

## S. MVP DEMO PATH (§103)

1. Open the marketing homepage → hero states the problem
2. Scroll: Recover / **Grow (Most Popular)** / Scale with real prices
3. Open **Pricing** → the comparison table, rendered from the public projection
4. Click **Grow** → Growth Review form opens with `interested_plan = Grow` prefilled (§96)
5. Submit a clearly synthetic demo store (§104 — never real PII in fixtures)
6. Switch to the operator console → **the prospect appears**
7. Operator adds a note, sets status, selects **Grow**
8. **Convert to Tenant** → org + owner + subscription + jewelry pack, owner invited
9. Operator sees onboarding readiness; owner logs in to guided setup

Every step uses real code paths; **nothing bypasses security for the demo** (§103).

---

## OPEN QUESTIONS

1. **Domain** not yet decided → config-driven from day one (§63). Confirm when known.
2. **Activation fee** ₹2,999 "waived for pilot" (§17) — plan metadata now, or defer?
3. **Scheduling** (§88): MVP is "operator contacts prospect" — confirm no Calendly.
4. **WhatsApp CTA** (§62) needs a **business** number in config — not a personal founder number.
5. Six product-truth decisions from the entitlements review (appointment booking, segmentation,
   Support Agent, competitor watchlist, analytics split, nurture) — these also govern what the
   **website may claim** (§93), so they block honest pricing copy.
