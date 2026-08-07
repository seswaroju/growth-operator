// Presentation helpers for the approvals queue — pure + unit-tested, so ApprovalsSection stays a
// thin renderer. `action_type` is the tool name the mediation proxy parked; the payload is that
// tool's args (for messages.send: the reply `body`, plus `amount_minor` when it's a priced quote).

import type { Approval } from "../api";

// A human title for what's waiting. A messages.send carrying a price is really a quote.
export function actionLabel(a: Pick<Approval, "action_type" | "payload">): string {
  switch (a.action_type) {
    case "messages.send":
      return a.payload.amount_minor != null ? "Send quote" : "Reply to customer";
    case "campaigns.execute":
      return "Send campaign";
    case "catalog.write":
      return "Update catalog";
    default:
      return a.action_type;
  }
}

// The draft text the owner reviews/edits — the message body when present.
export function draftText(payload: Record<string, unknown>): string {
  const body = payload.body ?? payload.text ?? payload.message;
  return typeof body === "string" ? body : "";
}

// ₹ price when the payload carries amount_minor (integer minor units → rupees).
export function priceLabel(payload: Record<string, unknown>): string | null {
  const minor = payload.amount_minor;
  if (typeof minor !== "number") return null;
  return "₹" + (minor / 100).toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

export function tierLabel(tier: number): string {
  return tier >= 3 ? "High-stakes" : "Needs your OK";
}

// Minutes-aware "expires in …" (or "expired") from an ISO timestamp, relative to `now`.
export function expiryLabel(expiresAtIso: string, now: number = Date.now()): string {
  const mins = Math.round((new Date(expiresAtIso).getTime() - now) / 60000);
  if (mins <= 0) return "expired";
  if (mins < 60) return `expires in ${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m ? `expires in ${h}h ${m}m` : `expires in ${h}h`;
}
