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
