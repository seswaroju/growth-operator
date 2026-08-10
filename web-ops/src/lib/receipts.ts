// Pure helpers for the operator payments surface (charge a store + request a receipt). Kept out of
// the component so the fast-refresh lint stays clean and the money math is unit-testable. The
// backend is the source of truth for the stored totals; these only drive the live form preview.

import type { Tone } from "./ui";

export interface DraftLine {
  description: string;
  amountRupees: string;
}

// A rupee string → integer paise (minor units). Blank/invalid → 0. Mirrors plans.rupeesToMinor.
export function toMinor(rupees: string): number {
  const n = Number.parseFloat(rupees);
  return Number.isFinite(n) ? Math.round(n * 100) : 0;
}

export function subtotalMinor(lines: DraftLine[]): number {
  return lines.reduce((sum, l) => sum + toMinor(l.amountRupees), 0);
}

export interface Totals {
  subtotal: number;
  discount: number;
  tax: number;
  total: number;
}

// Preview the receipt total the backend computes: subtotal − discount% + tax. The discount rounds
// half-up to whole paise, mirroring the server's Decimal ROUND_HALF_UP (subtotal·pct is positive, so
// JS Math.round — which rounds .5 up — matches).
export function previewTotals(
  lines: DraftLine[], discountPercent: string, taxRupees: string,
): Totals {
  const subtotal = subtotalMinor(lines);
  const pct = Number.parseFloat(discountPercent);
  const discount = Number.isFinite(pct) && pct > 0 ? Math.round((subtotal * pct) / 100) : 0;
  const tax = toMinor(taxRupees);
  return { subtotal, discount, tax, total: subtotal - discount + tax };
}

// At least one line needs a description AND a positive amount before a charge can be recorded.
export function hasChargeableLine(lines: DraftLine[]): boolean {
  return lines.some((l) => l.description.trim().length > 0 && toMinor(l.amountRupees) > 0);
}

export interface StatusView {
  label: string;
  tone: Tone;
}

// A transaction's status → label + tone. created = chargeable; paid = a receipt.send approval is
// pending the owner's decision; receipted = the receipt has been sent.
export function statusView(status: string): StatusView {
  switch (status) {
    case "receipted":
      return { label: "Receipt sent", tone: "good" };
    case "paid":
      return { label: "Receipt pending approval", tone: "warn" };
    default:
      return { label: "Awaiting receipt", tone: "muted" };
  }
}

// A receipt can only be requested once, for a freshly-created (not yet paid/receipted) transaction.
export function canRequestReceipt(status: string): boolean {
  return status === "created";
}
