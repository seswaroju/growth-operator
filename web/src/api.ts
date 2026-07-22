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
