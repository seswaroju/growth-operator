// Recovery outcome presentation (PILOT-1C). Pure + unit-tested.
//
// The one rule this file exists to enforce: never say something happened that we cannot prove.
// The API returns `sent`, `delivered` and `replied` as three separate timestamps because they are
// three different claims — handing a message to WhatsApp is ours to assert, delivery is WhatsApp's.
// Collapsing them into a single cheerful status is how a dashboard starts lying to the person
// paying for it.

import type { RecoveryAttempt, RecoverySummary } from "../api";

export type OutcomeTone = "good" | "warn" | "danger" | "muted" | "accent";

/** The one-word state, derived from the timestamps rather than trusted from `status`. */
export function outcomeOf(a: RecoveryAttempt): { label: string; tone: OutcomeTone } {
  if (a.replied_at) return { label: "Replied", tone: "good" };
  if (a.delivered_at) return { label: "Delivered", tone: "accent" };
  if (a.sent_at && a.status === "delivery_unknown") return { label: "Unconfirmed", tone: "warn" };
  if (a.sent_at) return { label: "Sent", tone: "accent" };
  if (a.owner_handled) return { label: "You handled it", tone: "muted" };
  if (a.status === "blocked") return { label: "Not sent", tone: "warn" };
  if (a.status === "failed") return { label: "Failed", tone: "danger" };
  if (a.status === "awaiting_approval") return { label: "Waiting for you", tone: "warn" };
  return { label: "Proposed", tone: "muted" };
}

/** Plain-language reasons. A code like `suppressed_contact` tells an owner nothing actionable. */
const BLOCK_REASON: Record<string, string> = {
  suppressed_contact: "They asked not to be messaged",
  consent_missing: "No marketing consent on file",
  template_not_sendable: "The WhatsApp template isn't approved yet",
  tenant_paused: "Messaging is paused for your store",
  provider_send_failed: "WhatsApp didn't accept the message",
  budget_exceeded: "Message budget reached",
};

export function explainBlock(reason: string | null): string | null {
  if (!reason) return null;
  return BLOCK_REASON[reason] ?? reason.replace(/_/g, " ");
}

/**
 * The reply rate, or null when there is nothing honest to divide by.
 *
 * Denominator is messages actually sent — not leads diagnosed, not attempts proposed. A rate
 * computed over attempts we never sent would flatter us for doing less. Under five sends there is
 * no rate worth showing: 1-in-2 is not a 50% reply rate, it is two customers.
 */
export function replyRate(s: RecoverySummary): number | null {
  if (s.sent < 5) return null;
  return Math.round((s.replied / s.sent) * 100);
}

/** True when delivery receipts are lagging enough that `delivered` would understate reality. */
export function deliveryPending(s: RecoverySummary): boolean {
  return s.sent > 0 && s.delivered < s.sent;
}
