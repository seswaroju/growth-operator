// Settings/autonomy presentation — pure + unit-tested. The knob is per-capability; at the approval
// gate the meaningful distinction is auto-send (auto) vs approve-everything (anything else), so the
// UI exposes two states — Auto / Review — mapping to "auto" / "draft_only".

export interface Capability {
  key: "messaging" | "pricing" | "campaigns";
  label: string;
  help: string;
}

export const CAPABILITIES: Capability[] = [
  { key: "messaging", label: "Customer replies", help: "Routine replies to customer messages." },
  { key: "pricing", label: "Quotes & pricing", help: "Price quotes sent to customers." },
  { key: "campaigns", label: "Campaigns", help: "Outbound campaign messages." },
];

// Friendly names for the immovable tier-4 floor (always owner-approved).
export const FLOOR_ACTION_LABEL: Record<string, string> = {
  "payment.charge": "Charge a payment",
  "payment.refund": "Issue a refund",
  "payout.create": "Send a payout",
  "supplier.order_commit": "Commit a supplier order",
  "gbp.update": "Update your Google listing",
  "ads.publish": "Publish an ad",
};

export function floorActionLabel(action: string): string {
  return FLOOR_ACTION_LABEL[action] ?? action.replace(/[._]/g, " ");
}

export function isAuto(level: string): boolean {
  return level === "auto";
}

// The value written when the owner picks Auto vs Review.
export const AUTO_VALUE = "auto";
export const REVIEW_VALUE = "draft_only";

export const REPLY_TONES = ["warm", "friendly", "professional", "formal"];
