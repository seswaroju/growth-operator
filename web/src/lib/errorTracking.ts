// Frontend error tracking to a SELF-HOSTED GlitchTip (Sentry ingest protocol) — security S2.
//
// INERT unless `VITE_ERROR_DSN` is set at build time: with no DSN, Sentry is never initialized and
// no event leaves the browser. When it IS set, PII is scrubbed before every send (mirrors the
// backend core/common/error_tracking.py) so a customer's phone/email/OTP never reaches the
// dashboard. No third-party SaaS — only our own GlitchTip receives errors.

import * as Sentry from "@sentry/react";

const DSN = import.meta.env.VITE_ERROR_DSN as string | undefined;

const PHONE = /\+[1-9]\d{7,14}/g;
const OTP = /(?<!\d)\d{6}(?!\d)/g;
const EMAIL = /[\w.+-]+@[\w-]+\.[\w.-]+/g;
// Keys whose values are dropped wholesale, wherever they appear in the event object.
const SENSITIVE_KEY = /^(authorization|cookies?|set-cookie|token|access_token|refresh_token|password|secret|code|otp|api_key|x-api-key|credentials?)$/i;

export function scrubText(text: string): string {
  return text
    .replace(PHONE, "[redacted-phone]")
    .replace(OTP, "[redacted-otp]")
    .replace(EMAIL, "[redacted-email]");
}

export function scrubDeep(value: unknown): unknown {
  if (typeof value === "string") return scrubText(value);
  if (Array.isArray(value)) return value.map(scrubDeep);
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) {
      out[k] = SENSITIVE_KEY.test(k) ? "[redacted]" : scrubDeep(v);
    }
    return out;
  }
  return value;
}

// Initialize error tracking iff a DSN is configured. Returns whether it was activated.
export function initErrorTracking(): boolean {
  if (!DSN) return false;
  Sentry.init({
    dsn: DSN,
    sendDefaultPii: false, // never attach IP / cookies / headers by default
    beforeSend: (event) => scrubDeep(event) as Sentry.ErrorEvent,
    beforeBreadcrumb: (crumb) => scrubDeep(crumb) as typeof crumb,
  });
  return true;
}

// Report a caught error (no-op when tracking isn't initialized). Scrubbing happens in beforeSend.
export function reportError(error: unknown, context?: Record<string, unknown>): void {
  Sentry.captureException(error, context ? { extra: context } : undefined);
}
