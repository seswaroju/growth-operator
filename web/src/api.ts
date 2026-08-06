// Auth API client for the MVP-011 email-OTP endpoints.
// Base URL is configurable via VITE_API_BASE (defaults to the local dev backend).

const BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000";

export type OtpRequestResult =
  | { ok: true; code?: string }
  | { ok: false; status: number; detail: string };

export type VerifyResult =
  | { ok: true; accessToken: string; refreshToken: string }
  | { ok: false; status: number; detail: string };

const UNREACHABLE =
  "Cannot reach the API — is the backend running on " +
  BASE +
  "? (Toggle Simulate to preview the flow without a backend.)";

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

// ---- Simulate mode (no backend) --------------------------------------------
// Mirrors the server contract so the UX is demoable with zero infrastructure.
// The generated code is surfaced to the UI the same way the dev-echo adapter would.

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

let simCode: string | null = null;
let simAttempts = 0;

export function simulateRequestOtp(identifier: string): OtpRequestResult {
  if (!EMAIL_RE.test(identifier)) {
    return { ok: false, status: 422, detail: "identifier must be a valid email address" };
  }
  simCode = String(Math.floor(Math.random() * 1_000_000)).padStart(6, "0");
  simAttempts = 0;
  return { ok: true, code: simCode };
}

export function simulateVerifyOtp(identifier: string, code: string): VerifyResult {
  if (!EMAIL_RE.test(identifier)) {
    return { ok: false, status: 422, detail: "identifier must be a valid email address" };
  }
  if (simCode == null) {
    return { ok: false, status: 401, detail: "Invalid or expired code." };
  }
  if (simAttempts >= 5) {
    return { ok: false, status: 429, detail: "too many attempts; request a new code" };
  }
  if (code !== simCode) {
    simAttempts += 1;
    return { ok: false, status: 401, detail: "Invalid or expired code." };
  }
  simCode = null;
  return {
    ok: true,
    accessToken: "sim.access.token.(demo-only)",
    refreshToken: "sim.refresh.token.(demo-only)",
  };
}

export const API_BASE = BASE;

// ---- Support tickets -------------------------------------------------------
// The store owner (any signed-in org member) raises issues; a platform-admin resolves them across
// tenants. `adminListTickets` returns 403 for a non-operator — the console uses that to decide
// whether to show the operator queue at all.

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

export interface AdminTicket extends Ticket {
  org_id: string;
  org_name: string;
  raised_by: string | null;
}

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
        ...(init?.headers ?? {}),
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
