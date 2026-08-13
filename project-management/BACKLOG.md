# Backlog

Everything known-but-not-built, so nothing is lost between sessions. **This is a queue, not a
commitment** — the founder picks the order; Claude never self-selects the next ticket (CLAUDE.md
§24.1) and never implements from a vision prompt without the approval gate.

Status: **DESIGN-REVIEW-FIRST** (founder requires a written review before code) · **NEXT** ·
**QUEUED** · **IDEA** · **BLOCKED**.

> **2026-08-12 — supersedes the earlier ENT-1b…1f sketch.** The founder delivered a full commercial
> vision (subscription packaging, entitlements, promotions, plan builder, announcement attachments)
> plus the start of a second one (public website / prospect → tenant conversion / guided onboarding).
> My earlier ad-hoc ENT-1b..1f items are folded into PLAN-1…PLAN-5 below.

---

## 0. ⚠️ Outstanding input needed from the founder

- **The second vision prompt was TRUNCATED at 50,000 characters.** Received: its §1 (founder intent —
  public marketing site, no self-service signup yet) and the §2 heading ("IMPORTANT SURFACE
  SEPARATION"). **Everything after that is missing** — presumably the site structure, prospect/lead
  capture, sales → tenant conversion, guided onboarding, and its own required-response section.
  **Action: founder to re-send part 2** (in chunks, or as a file in the vault I can read).
- **Design review owed before any implementation** (their §75 A–N). See PLAN-0 below.

---

## A. Commercial model — Recover / Grow / Scale (the founder's spec, §1–79)

**Commercial packaging (default, not hard-coded):**

| | Recover | Grow | Scale |
|---|---|---|---|
| Price | ₹3,999/mo | ₹6,999/mo | ₹12,999/mo |
| Promise | Stop losing existing leads | Generate + convert more demand | Run much of growth/ops with AI |
| Positioning | Entry wedge | **Most Popular** (hero) | Premium |
| Responsibility | revenue **protection** | revenue **generation** | growth + **operational leverage** |
| Staff seats | 2 | 5 | 10 |

The architecture must support arbitrary further plans (Pilot, Custom, Launch Offer, Enterprise,
Jewelry Premium, Association, Founder…) built from the *same* entitlement system.

- **PLAN-0 — Design review (§75 A–N)** · **DESIGN-REVIEW-FIRST — THE NEXT DELIVERABLE**
  A written review, before any code, covering: (A) existing architecture to reuse, (B) capability
  inventory table, (C) Recover/Grow/Scale vs **actual repo readiness**, (D) persistence
  recommendation (`config` JSONB vs normalized tables), (E) machine-readable default plans,
  (F) entitlement resolver design, (G) what gets gated now vs follow-up, (H) promotion semantics
  (start-inclusive / end-exclusive / UTC), (I) announcement attachment architecture without coupling
  notifications→WhatsApp, (J) UI changes, (K) files affected, (L) backward compatibility for existing
  `features` / `config.agents` / `config.channels` / `config.addons`, (M) test categories,
  (N) ticket slicing. **Plus the §74 honesty table** (capability · repo evidence · maturity ·
  sellable? · proposed plan).

- **PLAN-1 — Capability catalog + structured entitlement contract** · **QUEUED**
  One authoritative registry: `key, label, description, category, kind, status, dependencies,
  commercial_visibility`. Kinds: `feature | agent | channel | channel_capability | addon | limit`.
  Statuses: `available | beta | planned`. **Customer-perceivable capabilities only** — never
  infrastructure (RLS, Redis, outbox, LangGraph…). Vertical packs (L1) may contribute
  vertical-specific commercial capabilities (e.g. gold/rate operations) **without** polluting L0
  (Rule Zero). *Extends ENT-1a's catalog, which was a first cut.*

- **PLAN-2 — Effective entitlement resolver + backward compatibility** · **QUEUED**
  `effective_entitlements(org_id)` / `is_entitled(org_id, key)` = active subscription → plan
  permanent entitlements + currently-active promotions (+ future tenant overrides). Centralized —
  no `if plan.config["foo"]` scattered around. Compatibility loader for existing `features` /
  `config.*`; an unmappable legacy value is **preserved as legacy/custom display**, never discarded.
  *Extends ENT-1a's `entitlements()`.*

- **PLAN-3 — Recover/Grow/Scale presets + operator plan builder UI** · **QUEUED**
  Structured builder replacing the free-text/comma-separated editor: grouped + searchable capability
  selection, **start-from-existing-plan copy (explicit snapshot, NO live inheritance)**, staff limit,
  positioning metadata (`recommended`), plan **preview showing the business promise** (not machine
  keys), and a **subscriber-impact confirmation** before material removals ("12 active subscribers;
  you're removing Campaigns"). Channels come from the **existing channel registry**; "all channels"
  saves an explicit **snapshot**, never `"*"`.

- **PLAN-4 — Temporary promotional entitlements** · **QUEUED**
  Plan-level entitlement with `source=promotion`, `starts_at`, `ends_at`, `promotion_label`.
  **Expiry is time-derived, never cron-dependent**: `enabled AND starts_at <= now AND (ends_at IS
  NULL OR now < ends_at)`, UTC, **start inclusive / end exclusive**. Expired promos stay visible to
  the operator as history but are not effective. Designed so **tenant-specific** overrides remain
  possible later without rework.

- **PLAN-5 — Initial runtime enforcement (prove the model, don't retrofit everything)** · **QUEUED**
  Gate enough key capabilities to demonstrate correctness; **explicitly list what remains ungated**
  rather than claiming complete coverage (§35). Entitlements never bypass approvals/tool
  grants/execution tokens/budgets/RLS/consent — those stay independent (§57).
  *ENT-1a already gates `landing_pages` + `campaigns.whatsapp`.*

**Commercial invariants to honour throughout:** planned features are never sellable (SEO/AEO/GEO =
**planned, do not sell**); Google Ads = **Beta** if it only creates gated/paused campaigns; channel
*entitlement* ≠ channel *connection*; Meta messaging fees and ad spend are **billed separately**;
machine keys ≠ marketing copy; no coupon/discount/metered-billing engine.

---

## B. Announcement attachments

- **ANNOUNCE-1 — Generic media extraction + attachments** · **QUEUED**
  Extend the **existing** announcements system (no `broadcasts_v2`). Multiple `attachments[]` (cap
  ~5). Accept `image/jpeg`, `image/png`, `image/webp`, `application/pdf`; reject SVG/HTML/JS/EXE;
  server-side MIME validation, size cap, SHA-256, **AV scan fail-closed**, object storage. Metadata
  row only — **bytes never in Postgres**. Evaluate extracting platform-generic media primitives out
  of `core/channels/whatsapp/media.py` into `core/media/` so `core/notifications` does **not** depend
  on WhatsApp (shared consumers: WhatsApp media, announcement attachments, landing assets, campaign
  creative).
- **ANNOUNCE-2 — Owner rendering + controlled access** · **QUEUED**
  Image preview / PDF card in the owner feed; **never expose `s3://` refs** — authenticated
  download/view endpoint. Archive/retract behaviour preserved; attachment history retained for audit.

---

## C. Public website / acquisition / onboarding (spec **INCOMPLETE — see §0**)

- **WEB-0 — Receive the full spec, then design review** · **BLOCKED (founder to re-send part 2)**
  Known so far: a public company/product site so a visitor is **not** dropped on a login screen;
  must explain what GO is, the problem, differentiation, Recover/Grow/Scale + pricing, how the AI
  works, trust, what genuinely exists, how to talk to us, how to become a customer, and where
  existing customers log in. **Not** a self-service SaaS signup yet, and **not** an enormous
  marketing-site project. Requires clear **surface separation** between marketing site and app.
  Reconcile with the commercial model above (pricing page should render from plan/catalog metadata,
  not duplicated copy — §67).

---

## D. Product tracks already in flight (unchanged)

- **LP-4c — Upload widget (owner screen)** · **QUEUED** — hero (required) + up to 4 product photos →
  3 layouts. Endpoint shipped in LP-4b; this is the front door.
- **LP-4d — "Your pages are ready" notification** · **QUEUED**
- **LP-3c — UTM/variant attribution + `landing_page.*` outbox events** · **QUEUED**
- **GHOST-2 — Sales-handoff branch** (escalate to a named salesperson) · **QUEUED**
- **GHOST-3 — Recovery outcome analytics** (which reasons convert → revenue) · **IDEA**
- **CAMP-1 — Ad launch with HITL #2** (agent proposes → owner approves → gated adapters → analysis
  window) · **QUEUED**
- **DOM-\* — Custom domains + TLS + pretty URLs** · **BLOCKED** (hosting, #8/#10)

---

## E. Go-live wiring (mostly founder-side)

| Blocker | Needs |
|---|---|
| **#3** WhatsApp/Meta | Number decision + WABA verification; code is real-ready (flag flip + connect) |
| **#6** Razorpay | Company entity + account; adapter gated/simulated |
| **#8 / #10** Hosting + residency | India VPS vs Hetzner; staging env. Blocks live public serving + domains |
| **#5** IBJA rate | Flip `rates_provider_enabled`, verify a known day's rate |
| **#16** Embeddings | Flip `embeddings_provider_enabled` + operator OpenAI key |
| **#26** CP-4 follow-ups | Ad adapters read **per-store** creds at send time; operator-console auto-logout; per-provider LLM keys; override failover |
| **#14** Pack installer | Attribute freeze + credential revocation on uninstall |
| **#4** Dependencies | Unused/missing deps review |

---

## F. Documentation sync (vault, founder-edited)

- **#21** Support-tickets track narrative · **BLOCKED (founder)**
- **#23** CRM-depth tables (migration 040) reconciliation · **BLOCKED (founder)**
  Same 5-minute edits as #27/#29 (both done). Claude can supply exact text on request.

---

## G. Known local-environment noise (not product defects)

- **#22b** `test_rate_ingestion` fails on the **local** dev DB from stale seed pollution; passes on a
  fresh DB and in CI. Fix = reset the local DB, not code.
