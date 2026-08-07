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

export const AVAILABILITY_STYLE: Record<string, string> = {
  in_stock: "bg-green-100 text-green-800",
  made_to_order: "bg-amber-100 text-amber-800",
  out_of_stock: "bg-neutral-200 text-neutral-600",
};

// Rupees (as typed by the owner) → integer minor units for the API. NaN/empty → null.
export function rupeesToMinor(rupees: string): number | null {
  const n = Number.parseFloat(rupees);
  return Number.isFinite(n) ? Math.round(n * 100) : null;
}
