// CRM presentation helpers — pure + unit-tested.

export const CONSENT_LABEL: Record<string, string> = {
  unknown: "Consent unknown",
  opted_in: "Opted in",
  opted_out: "Opted out",
  pending: "Consent pending",
};

export function consentLabel(s: string): string {
  return CONSENT_LABEL[s] ?? s.replace(/_/g, " ");
}

// Harmonized to the design tokens (opted_in=good, opted_out=danger, pending=warn, unknown=muted).
export const CONSENT_STYLE: Record<string, string> = {
  opted_in: "bg-good-soft text-good",
  opted_out: "bg-danger-soft text-danger",
  pending: "bg-warn-soft text-warn",
  unknown: "bg-line-2 text-ink-2",
};

export const ORDER_STATUS_LABEL: Record<string, string> = {
  placed: "Placed",
  in_progress: "In progress",
  ready: "Ready",
  delivered: "Delivered",
  returned: "Returned",
};

export function orderStatusLabel(s: string): string {
  return ORDER_STATUS_LABEL[s] ?? s.replace(/_/g, " ");
}

// Integer minor units → a currency string (₹ for INR).
export function money(minor: number, currency: string): string {
  const symbol = currency === "INR" ? "₹" : `${currency} `;
  return symbol + (minor / 100).toLocaleString("en-IN", { maximumFractionDigits: 2 });
}
