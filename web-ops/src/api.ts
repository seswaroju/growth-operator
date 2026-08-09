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

export interface BillingPlan {
  id: string;
  name: string;
  price_minor: number;
  active: boolean;
  created_at: string;
}

export interface Subscription {
  id: string;
  plan_id: string;
  plan_name: string;
  price_minor: number;
  status: string;
  started_at: string;
}

export type ChargeType = "subscription" | "social" | "seo" | "campaign" | "other";

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
): Promise<BillingPlan> {
  return authed<BillingPlan>("/v1/admin/billing/plans", token, {
    method: "POST",
    body: JSON.stringify({ name, price_minor: priceMinor }),
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
