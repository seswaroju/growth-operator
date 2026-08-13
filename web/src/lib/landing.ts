/** Landing-page presentation helpers (LP-4a) — pure, unit-tested. */

export const LANDING_STATUSES = [
  "draft", "generated", "validated", "awaiting_approval",
  "approved", "published", "paused", "archived",
] as const;

export type LandingStatusName = (typeof LANDING_STATUSES)[number];

export const STATUS_LABEL: Record<string, string> = {
  draft: "Draft",
  generated: "Ready to review",
  validated: "Validated",
  awaiting_approval: "Awaiting approval",
  approved: "Approved",
  published: "Live",
  paused: "Paused",
  archived: "Archived",
};

/** Chip tone per status — "Live" reads positive, anything needing you reads warn. */
export function statusTone(status: string): "good" | "warn" | "muted" | "accent" {
  if (status === "published") return "good";
  if (status === "generated" || status === "awaiting_approval") return "warn";
  if (status === "approved") return "accent";
  return "muted";
}

export function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status;
}

/** Which lifecycle actions make sense from here (mirrors the server's transition map). */
export function availableActions(status: string): Array<"publish" | "pause" | "archive"> {
  if (status === "approved" || status === "paused") return ["publish", "archive"];
  if (status === "published") return ["pause", "archive"];
  if (status === "archived") return [];
  return ["archive"];
}

/** A human sentence for a variant, e.g. "classic — the full page". */
export const VARIANT_BLURB: Record<string, string> = {
  classic: "The full page — every section",
  focused: "Short and punchy — straight to the offer",
  story: "Trust-led — proof and benefits first",
  catalog: "Product-first — the range up front",
  objection: "Answers the doubts before the products",
  default: "Single generated page",
};

export function variantBlurb(label: string): string {
  return VARIANT_BLURB[label] ?? "Alternative layout";
}

/** LP-4b: how many layouts a page may have. Default 3 — a typical page needs no more. */
export const DEFAULT_VARIANTS = 3;
export const MAX_VARIANTS = 5;
