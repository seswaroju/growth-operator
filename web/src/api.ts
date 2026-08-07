// API client for the CUSTOMER app (store owner / manager / staff / viewer).
// Talks to the local dev backend by default. The Growth Operator (operator) API lives in the
// SEPARATE web-ops app — none of it ships here.

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

// ---- Identity (/v1/me) -----------------------------------------------------

export interface Me {
  user: { id: string; email: string | null; phone: string | null; full_name: string | null };
  org: { id: string; name: string } | null;
  roles: string[];
}

export function getMe(token: string): Promise<Me> {
  return authed<Me>("/v1/me", token);
}

// ---- Dashboard overview (/v1/dashboard/overview, insights:read) -------------

export interface Overview {
  pending_approvals: number;
  open_conversations: number;
  catalog_items: number;
  open_tickets: number;
}

export function getOverview(token: string): Promise<Overview> {
  return authed<Overview>("/v1/dashboard/overview", token);
}

// ---- Approvals (/v1/approvals, approvals:read / approvals:resolve) ----------

export interface Approval {
  id: string;
  run_id: string | null;
  action_type: string;
  tier: number;
  payload: Record<string, unknown>;
  matched_rules: string[];
  status: string;
  expires_at: string;
  created_at: string;
}

export interface ResolveResult {
  approval_id: string;
  status: string;
  tier: number;
  edited: boolean;
  idempotent_replay: boolean;
  note: string | null;
}

export interface ResolveInput {
  decision: "approve" | "reject";
  edited_payload?: Record<string, unknown> | null;
  reason_code?: string | null;
  note?: string | null;
}

export function listApprovals(token: string, status = "pending"): Promise<Approval[]> {
  return authed<Approval[]>(`/v1/approvals?status_filter=${encodeURIComponent(status)}`, token);
}

export function resolveApproval(
  token: string, id: string, input: ResolveInput,
): Promise<ResolveResult> {
  return authed<ResolveResult>(`/v1/approvals/${id}/resolve`, token, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

// ---- Conversations & leads (/v1/conversations, /v1/leads, conversations:read)

export interface ConvLastMessage {
  body: string | null;
  direction: string | null;
  at: string | null;
}

export interface ConversationSummary {
  id: string;
  contact_name: string | null;
  contact_phone: string | null;
  status: string;
  outcome: string | null;
  message_count: number;
  last_message: ConvLastMessage | null;
  updated_at: string;
}

export interface Message {
  id: string;
  direction: string | null;
  body: string | null;
  status: string;
  template_key: string | null;
  created_at: string;
}

export interface ConversationDetail {
  id: string;
  contact_name: string | null;
  contact_phone: string | null;
  status: string;
  outcome: string | null;
  created_at: string;
  updated_at: string;
  messages: Message[];
}

export interface Lead {
  id: string;
  stage: string;
  source: string;
  score: number | null;
  contact_name: string | null;
  contact_phone: string | null;
  next_followup_at: string | null;
  updated_at: string;
}

export function getConversations(token: string): Promise<ConversationSummary[]> {
  return authed<ConversationSummary[]>("/v1/conversations", token);
}

export function getConversation(token: string, id: string): Promise<ConversationDetail> {
  return authed<ConversationDetail>(`/v1/conversations/${id}`, token);
}

export function getLeads(token: string): Promise<Lead[]> {
  return authed<Lead[]>("/v1/leads", token);
}

// ---- Support tickets (owner / manager / staff) -----------------------------

export type TicketSeverity = "minor" | "major" | "critical";
export type TicketPriority = "low" | "normal" | "high" | "urgent";
export type TicketStatus = "open" | "in_progress" | "resolved" | "closed";
export type TicketCategory =
  | "whatsapp" | "catalog" | "pricing" | "billing" | "account" | "other";

export interface Ticket {
  id: string;
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

export interface RaiseTicketInput {
  subject: string;
  description: string;
  category: TicketCategory;
  severity: TicketSeverity;
}

export function raiseTicket(token: string, input: RaiseTicketInput): Promise<Ticket> {
  return authed<Ticket>("/v1/support/tickets", token, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function listMyTickets(token: string): Promise<Ticket[]> {
  return authed<Ticket[]>("/v1/support/tickets", token);
}

// ---- Team / invites (members:invite) ---------------------------------------

export interface CreateInviteInput {
  role: string;
  identifier?: string | null;
}

export interface Invite {
  id: string;
  expires_at: string;
  invite_token: string;
}

export function createInvite(token: string, input: CreateInviteInput): Promise<Invite> {
  return authed<Invite>("/v1/orgs/invites", token, {
    method: "POST",
    body: JSON.stringify(input),
  });
}
