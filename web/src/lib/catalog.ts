// Catalog presentation helpers — pure + unit-tested. `price_mode` is 'static' (a fixed
// base_price_minor) or 'computed' (priced live at quote time from a live rate source).

import type { CatalogItem } from "../api";

export function priceLabel(
  item: Pick<CatalogItem, "price_mode" | "base_price_minor" | "currency">,
): string {
  if (item.price_mode === "computed") return "Live rate";
  if (item.base_price_minor == null) return "—";
  const symbol = item.currency === "INR" ? "₹" : `${item.currency} `;
  return symbol + (item.base_price_minor / 100).toLocaleString("en-IN", {
    maximumFractionDigits: 2,
  });
}

export const AVAILABILITY_LABEL: Record<string, string> = {
  in_stock: "In stock",
  made_to_order: "Made to order",
  out_of_stock: "Out of stock",
};

export function availabilityLabel(a: string): string {
  return AVAILABILITY_LABEL[a] ?? a.replace(/_/g, " ");
}

// Harmonized to the design tokens (in_stock=good, made_to_order=warn, out_of_stock=muted).
export const AVAILABILITY_STYLE: Record<string, string> = {
  in_stock: "bg-good-soft text-good",
  made_to_order: "bg-warn-soft text-warn",
  out_of_stock: "bg-line-2 text-ink-2",
};

// Rupees (as typed by the owner) → integer minor units for the API. NaN/empty → null.
export function rupeesToMinor(rupees: string): number | null {
  const n = Number.parseFloat(rupees);
  return Number.isFinite(n) ? Math.round(n * 100) : null;
}
