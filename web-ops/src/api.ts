// API client for the OPERATOR app (Growth Operator staff: dev / admin / staff / analyst).
// Cross-tenant operator endpoints (/v1/admin/*). Separate from the customer app.

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000";

export const API_BASE = BASE;

const UNREACHABLE = `Cannot reach the API — is the backend running on ${BASE}?`;

// ---- Auth (email OTP) ------------------------------------------------------

export type OtpRequestResult = { ok: true } | { ok: false; status: number; detail: string };
export type VerifyResult =
  | { ok: true; accessToken: string; refreshToken: string }
  | { ok: false; status: number; detail: string };

export async function requestOtp(identifier: string): Promise<OtpRequestResult> {
  try {
    const res = await fetch(`${BASE}/v1/auth/otp`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ identifier }),
    });
    if (res.status === 202) return { ok: true };
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    return { ok: false, status: res.status, detail: body.detail ?? "Request failed" };
  } catch {
    return { ok: false, status: 0, detail: UNREACHABLE };
  }
}

export async function verifyOtp(identifier: string, code: string): Promise<VerifyResult> {
  try {
    const res = await fetch(`${BASE}/v1/auth/otp/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ identifier, code }),
    });
    if (res.status === 200) {
      const body = (await res.json()) as { access_token: string; refresh_token: string };
      return { ok: true, accessToken: body.access_token, refreshToken: body.refresh_token };
    }
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    return { ok: false, status: res.status, detail: body.detail ?? "Verification failed" };
  } catch {
    return { ok: false, status: 0, detail: UNREACHABLE };
  }
}

// ---- Authenticated helper --------------------------------------------------

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function authed<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError(0, UNREACHABLE);
  }
  if (res.status === 204) return undefined as T;
  const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) {
    const detail = typeof body.detail === "string" ? body.detail : `Request failed (${res.status})`;
    throw new ApiError(res.status, detail);
  }
  return body as T;
}

// ---- Operator identity (/v1/admin/me) --------------------------------------

export interface AdminMe {
  user_id: string;
  role: string;
  permissions: string[];
}

// Throws ApiError with status 404 (plane disabled) or 403 (not an operator) — the auth layer
// distinguishes them.
export function getAdminMe(token: string): Promise<AdminMe> {
  return authed<AdminMe>("/v1/admin/me", token);
}

// ---- Support queue (cross-tenant) ------------------------------------------

export type TicketSeverity = "minor" | "major" | "critical";
export type TicketPriority = "low" | "normal" | "high" | "urgent";
export type TicketStatus = "open" | "in_progress" | "resolved" | "closed";

export interface AdminTicket {
  id: string;
  org_id: string;
  org_name: string;
  raised_by: string | null;
  subject: string;
  description: string;
  category: string;
  priority: TicketPriority;
  severity: TicketSeverity;
  status: TicketStatus;
  resolution_note: string | null;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
}

export function adminListTickets(token: string): Promise<AdminTicket[]> {
  return authed<AdminTicket[]>("/v1/admin/support/tickets", token);
}

export interface UpdateTicketInput {
  status?: TicketStatus;
  priority?: TicketPriority;
  resolution_note?: string;
}

export function adminUpdateTicket(
  token: string,
  id: string,
  patch: UpdateTicketInput,
): Promise<AdminTicket> {
  return authed<AdminTicket>(`/v1/admin/support/tickets/${id}`, token, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

// ---- Cross-store roster (/v1/admin/tenants, platform.tenants:read) ----------
// Curated registry + counts per store — never customer data. Backed by the platform_tenant_roster()
// SECURITY DEFINER function; every listing is audited server-side.

export interface TenantRosterRow {
  org_id: string;
  name: string;
  plan: string | null;
  status: string;
  created_at: string;
  paused: boolean;
  open_tickets: number;
  member_count: number;
}

export function adminListTenants(token: string): Promise<TenantRosterRow[]> {
  return authed<TenantRosterRow[]>("/v1/admin/tenants", token);
}

export interface StoreCreated {
  org_id: string;
  owner_id: string;
  owner_existed: boolean;
  plan_id: string;
}

// Provision a store: creates the org + owner + subscription and emails the owner a setup link (CP-2).
export function adminCreateStore(
  token: string, input: { name: string; owner_email: string; plan_id: string },
): Promise<StoreCreated> {
  return authed<StoreCreated>("/v1/admin/tenants", token, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

// ---- Operational health (/v1/admin/ops/health, platform.tenants:read) -------
// Platform-wide "what's breaking / delayed" COUNTS only — never any store's rows. Error DETAIL lives
// in the self-hosted GlitchTip (security S2), not here.

export interface OperationalHealth {
  outbox_pending: number;
  outbox_stuck: number;
  approvals_pending: number;
  approvals_overdue: number;
  tickets_open: number;
  tickets_urgent: number;
  stores_paused: number;
}

export function adminOpsHealth(token: string): Promise<OperationalHealth> {
  return authed<OperationalHealth>("/v1/admin/ops/health", token);
}

// ---- Cross-store analytics rollup (/v1/admin/analytics/rollup, platform.tenants:read) -------
// Platform-wide SUMS/COUNTS for the Executive + Marketing views (+prev window for WoW). Aggregates
// only — no store's rows or customer data. CAC/churn + impressions/CPL are deferred (need billing /
// ad-platform data), not faked.

export interface AnalyticsRollup {
  period_days: number;
  revenue_minor: number;
  revenue_minor_prev: number;
  orders: number;
  orders_prev: number;
  leads: number;
  leads_prev: number;
  quotes: number;
  quotes_prev: number;
  active_stores: number;
  campaigns_run: number;
  messages_sent: number;
  campaigns_analyzed: number;
  attributed_revenue_minor: number;
}

export function adminAnalyticsRollup(token: string, days = 7): Promise<AnalyticsRollup> {
  return authed<AnalyticsRollup>(`/v1/admin/analytics/rollup?days=${days}`, token);
}

// ---- Customer-success health (/v1/admin/customer-health, platform.tenants:read) -------------
// Per-store aggregate health + a computed at_risk flag (at-risk first). Aggregate signals only —
// never a store's customer data. NPS + upsell are deferred (need surveys / billing data).

export interface StoreHealth {
  org_id: string;
  name: string;
  paused: boolean;
  open_tickets: number;
  urgent_tickets: number;
  resolved_7d: number;
  days_since_activity: number | null;
  revenue_7d: number;
  revenue_prev_7d: number;
  at_risk: boolean;
  churn_score: number; // 0–100 composite (OC5); higher = more likely to churn
  churn_band: "low" | "medium" | "high";
  churn_factors: string[]; // plain-language reasons, highest-weight first
}

export function adminCustomerHealth(token: string): Promise<StoreHealth[]> {
  return authed<StoreHealth[]>("/v1/admin/customer-health", token);
}

// ---- Per-store drill-down: a store's insight reports (/v1/admin/tenants/{org}/reports) -------
// platform.insights:read; every read is audited server-side with the target org.

export interface StoreReportSummary {
  id: string;
  report_type: string;
  subject_ref: string | null;
  title: string;
  verdict: string;
  confidence: string | null;
  generated_at: string;
}

export interface StoreReportDetail extends StoreReportSummary {
  drivers: { label: string; detail: string; sentiment: string }[];
  full_breakdown: Record<string, unknown>;
  evidence: unknown[];
  model: string | null;
  prompt_version: string | null;
}

export function adminStoreReports(token: string, orgId: string): Promise<StoreReportSummary[]> {
  return authed<StoreReportSummary[]>(`/v1/admin/tenants/${orgId}/reports`, token);
}

export interface StoreAnalytics {
  period_days: number;
  revenue_minor: number;
  revenue_minor_prev: number;
  orders: number;
  orders_prev: number;
  leads: number;
  leads_prev: number;
  quotes: number;
  quotes_prev: number;
  campaigns_run: number;
  messages_sent: number;
  campaigns_analyzed: number;
  attributed_revenue_minor: number;
}

export function adminStoreAnalytics(
  token: string, orgId: string, days = 30,
): Promise<StoreAnalytics> {
  return authed<StoreAnalytics>(`/v1/admin/tenants/${orgId}/analytics?days=${days}`, token);
}

export function adminStoreReport(
  token: string, orgId: string, reportId: string,
): Promise<StoreReportDetail> {
  return authed<StoreReportDetail>(`/v1/admin/tenants/${orgId}/reports/${reportId}`, token);
}

// ---- Billing (/v1/admin/billing/*, platform.tenants:read / :manage) ---------
// Operator-owned per-client revenue. Rollup feeds the Financial dashboard; plans + per-client
// subscription/charges are the management surface (writes need tenants:manage; audited server-side).

export interface BillingRollup {
  mrr_minor: number;
  charges_revenue_minor: number;
  charges_cost_minor: number;
  margin_minor: number;
  active_clients: number;
}

// Functional gating a plan turns on (CP-1). `llm` (per-agent model defaults) arrives in CP-5.
export interface PlanConfig {
  agents?: string[];
  channels?: string[];
  addons?: string[];
}

export interface BillingPlan {
  id: string;
  name: string;
  price_minor: number;
  active: boolean;
  description: string | null;
  features: string[];
  max_managers: number;
  max_staff: number;
  config: PlanConfig;
  created_at: string;
}

export interface PlanInput {
  name: string;
  price_minor: number;
  active: boolean;
  description: string | null;
  features: string[];
  max_managers: number;
  max_staff: number;
  config: PlanConfig;
}

export interface Subscription {
  id: string;
  plan_id: string;
  plan_name: string;
  price_minor: number;
  status: string;
  started_at: string;
}

export type ChargeType =
  | "subscription" | "social" | "seo" | "campaign" | "other"
  | "whatsapp" | "instagram" | "google_ads";

export interface BillingCharge {
  id: string;
  org_id: string;
  period_month: string;
  charge_type: string;
  amount_minor: number;
  cost_minor: number;
  note: string | null;
  created_at: string;
}

export interface ChargeInput {
  period_month: string;
  charge_type: ChargeType;
  amount_minor: number;
  cost_minor: number;
  note?: string | null;
}

export function adminBillingRollup(token: string): Promise<BillingRollup> {
  return authed<BillingRollup>("/v1/admin/billing/rollup", token);
}

export function adminListPlans(token: string): Promise<BillingPlan[]> {
  return authed<BillingPlan[]>("/v1/admin/billing/plans", token);
}

export function adminCreatePlan(
  token: string, name: string, priceMinor: number,
  extra?: {
    description?: string | null; features?: string[];
    max_managers?: number; max_staff?: number; config?: PlanConfig;
  },
): Promise<BillingPlan> {
  return authed<BillingPlan>("/v1/admin/billing/plans", token, {
    method: "POST",
    body: JSON.stringify({
      name, price_minor: priceMinor,
      description: extra?.description ?? null, features: extra?.features ?? [],
      max_managers: extra?.max_managers ?? 0, max_staff: extra?.max_staff ?? 0,
      config: extra?.config ?? {},
    }),
  });
}

export function adminUpdatePlan(
  token: string, planId: string, patch: PlanInput,
): Promise<BillingPlan> {
  return authed<BillingPlan>(`/v1/admin/billing/plans/${planId}`, token, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function adminGetSubscription(token: string, orgId: string): Promise<Subscription | null> {
  return authed<Subscription | null>(`/v1/admin/billing/tenants/${orgId}/subscription`, token);
}

export function adminAssignSubscription(
  token: string, orgId: string, planId: string,
): Promise<void> {
  return authed<void>(`/v1/admin/billing/tenants/${orgId}/subscription`, token, {
    method: "POST",
    body: JSON.stringify({ plan_id: planId }),
  });
}

export function adminListCharges(token: string, orgId: string): Promise<BillingCharge[]> {
  return authed<BillingCharge[]>(`/v1/admin/billing/tenants/${orgId}/charges`, token);
}

export function adminRecordCharge(
  token: string, orgId: string, input: ChargeInput,
): Promise<BillingCharge> {
  return authed<BillingCharge>(`/v1/admin/billing/tenants/${orgId}/charges`, token, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

// ---- Per-store transactions / receipts (PAY-TX + PAY3) ----------------------
// Operator charges a store (auto-numbered {STORE}-{YYMM}-seq, percent discount, notes) and can
// request a receipt — which DRAFTS a receipt.send approval; the branded receipt only goes out (email
// + WhatsApp) once it's approved. Writes need platform.tenants:manage; audited server-side.

export interface TxLineItem {
  description: string;
  amount_minor: number;
}

export interface Transaction {
  id: string;
  org_id: string;
  receipt_no: string;
  currency: string;
  line_items: TxLineItem[];
  subtotal_minor: number;
  discount_percent: number;
  discount_reason: string | null;
  discount_minor: number;
  tax_label: string;
  tax_minor: number;
  total_minor: number;
  notes: string | null;
  provider_ref: string | null;
  status: string;
  contact_email: string | null;
  contact_phone: string | null;
  created_at: string;
}

export interface NewTransactionInput {
  store_name: string;
  line_items: TxLineItem[];
  discount_percent: number;
  discount_reason?: string | null;
  tax_label: string;
  tax_minor: number;
  notes?: string | null;
  currency?: string;
  contact_email?: string | null;
  contact_phone?: string | null;
}

export interface ReceiptRequestResult {
  approval_id: string;
  receipt_no: string;
  status: string;
}

export function adminListTransactions(token: string, orgId: string): Promise<Transaction[]> {
  return authed<Transaction[]>(`/v1/admin/tenants/${orgId}/transactions`, token);
}

export function adminCreateTransaction(
  token: string, orgId: string, input: NewTransactionInput,
): Promise<Transaction> {
  return authed<Transaction>(`/v1/admin/tenants/${orgId}/transactions`, token, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function adminRequestReceipt(
  token: string, orgId: string, txId: string,
): Promise<ReceiptRequestResult> {
  return authed<ReceiptRequestResult>(
    `/v1/admin/tenants/${orgId}/transactions/${txId}/request-receipt`, token, { method: "POST" });
}

// ---- Per-channel budgets & caps (/v1/admin/billing/tenants/{org}/budgets, OC7) ----------------
// A monthly budget per channel with month-to-date spend + over flag; enforce=true pauses over-cap
// charges (429 budget_exceeded). Writes need platform.tenants:manage; audited server-side.

export interface BudgetStatus {
  charge_type: string;
  budget_minor: number;
  enforce: boolean;
  spent_minor: number;
  remaining_minor: number;
  pct: number | null;
  over: boolean;
}

export function adminListBudgets(token: string, orgId: string): Promise<BudgetStatus[]> {
  return authed<BudgetStatus[]>(`/v1/admin/billing/tenants/${orgId}/budgets`, token);
}

export function adminSetBudget(
  token: string, orgId: string, channel: string, budgetMinor: number, enforce: boolean,
): Promise<{ charge_type: string; budget_minor: number; enforce: boolean }> {
  return authed(`/v1/admin/billing/tenants/${orgId}/budgets/${channel}`, token, {
    method: "PUT",
    body: JSON.stringify({ budget_minor: budgetMinor, enforce }),
  });
}

export function adminDeleteBudget(token: string, orgId: string, channel: string): Promise<void> {
  return authed<void>(`/v1/admin/billing/tenants/${orgId}/budgets/${channel}`, token, {
    method: "DELETE",
  });
}

// ---- Monthly invoices from charges (/v1/admin/billing/tenants/{org}/invoices, OC12) -----------
// One statement per month with charges; amount only (never GO's cost). platform.tenants:read.

export interface InvoiceSummary {
  invoice_no: string;
  period_month: string; // "YYYY-MM"
  total_minor: number;
}

export interface Invoice extends InvoiceSummary {
  seller_name: string;
  buyer_name: string;
  currency: string;
  line_items: { charge_type: string; amount_minor: number }[];
}

export function adminListInvoices(token: string, orgId: string): Promise<InvoiceSummary[]> {
  return authed<InvoiceSummary[]>(`/v1/admin/billing/tenants/${orgId}/invoices`, token);
}

export function adminGetInvoice(token: string, orgId: string, month: string): Promise<Invoice> {
  return authed<Invoice>(`/v1/admin/billing/tenants/${orgId}/invoices/${month}`, token);
}
