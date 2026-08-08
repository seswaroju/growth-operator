// Owner outcome-card presentation — pure + unit-tested. The engine (business_metrics) exposes six
// metrics; the Home "This week" card shows the four owner-relevant outcomes with WoW deltas.

export const OUTCOME_METRICS = ["leads_created", "quotes_sent", "orders", "revenue_minor"] as const;

export const METRIC_LABEL: Record<string, string> = {
  leads_created: "New inquiries",
  quotes_sent: "Quotes sent",
  orders: "Orders",
  revenue_minor: "Revenue",
  messages_in: "Messages in",
  messages_out: "Messages out",
};

export function metricLabel(key: string): string {
  return METRIC_LABEL[key] ?? key.replace(/_/g, " ");
}

// Money metric (minor units) → ₹; everything else → a plain count.
export function formatValue(key: string, value: number): string {
  if (key === "revenue_minor") {
    return "₹" + (value / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 });
  }
  return value.toLocaleString("en-IN");
}

export type DeltaDir = "up" | "down" | "flat";

export function delta(deltaPct: number | null): { text: string; dir: DeltaDir } {
  if (deltaPct === null) return { text: "—", dir: "flat" };
  if (deltaPct > 0) return { text: `+${deltaPct}%`, dir: "up" };
  if (deltaPct < 0) return { text: `${deltaPct}%`, dir: "down" };
  return { text: "0%", dir: "flat" };
}
