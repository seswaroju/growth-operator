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

// ---- Insight records: verdict → drivers → breakdown → evidence (A4.6) -------
// The owner drills through an insight as escalating questions ("levels of intensity"), each one
// revealing a deeper layer of the record. Every answer is real stored data — no AI reply.

export type InsightLayer = "verdict" | "drivers" | "full_breakdown" | "evidence";

export interface QuestionLevel {
  level: number;
  question: string; // what the owner is effectively asking
  layer: InsightLayer; // the record layer that answers it
}

// Ordered by intensity: the deeper you go, the more the platform has to show its work.
export const QUESTION_LEVELS: QuestionLevel[] = [
  { level: 1, question: "What happened?", layer: "verdict" },
  { level: 2, question: "Why did it turn out this way?", layer: "drivers" },
  { level: 3, question: "Show me the numbers", layer: "full_breakdown" },
  { level: 4, question: "Prove it — show the evidence", layer: "evidence" },
];

export const REPORT_TYPE_LABEL: Record<string, string> = {
  campaign_analysis: "Campaign analysis",
  competitor_analysis: "Competitor analysis",
  marketing_strategy: "Marketing strategy",
};

export function reportTypeLabel(reportType: string): string {
  return REPORT_TYPE_LABEL[reportType] ?? reportType.replace(/_/g, " ");
}

export type Tone = "good" | "bad" | "neutral";

// A driver carries its own good/bad/neutral flag from the engine — surface it, don't re-derive it.
export function driverTone(sentiment: string): Tone {
  return sentiment === "good" || sentiment === "bad" ? sentiment : "neutral";
}

// Confidence is a real stored field (low/medium/high) — map it to a badge tone.
export function confidenceTone(confidence: string | null): Tone {
  if (confidence === "high") return "good";
  if (confidence === "low") return "bad";
  return "neutral"; // medium or unknown
}

// ---- Generic breakdown rendering (works across report types) ---------------
// full_breakdown shapes differ per report type, so format by key convention, not a fixed schema.

const BREAKDOWN_KEY_LABEL: Record<string, string> = {
  roas: "ROAS",
  roi_pct: "Return on spend",
  net_minor: "Net result",
  revenue_minor: "Revenue",
  cost_minor: "Cost",
  p_value: "p-value",
  lift_pct: "Lift vs. baseline",
  is_significant: "Statistically significant",
  campaign_rate: "Campaign conversion",
  baseline_rate: "Baseline conversion",
  window_days: "Attribution window (days)",
  drop_off: "Biggest drop-off",
};

export function humanizeBreakdownKey(key: string): string {
  return BREAKDOWN_KEY_LABEL[key] ?? key.replace(/_minor$/, "").replace(/_/g, " ");
}

// Format a single breakdown value by key convention: *_minor → ₹, *_pct → %, booleans, rates, else raw.
export function formatBreakdownValue(key: string, value: unknown): string {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    if (key.endsWith("_minor")) {
      return "₹" + (value / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 });
    }
    if (key.endsWith("_pct")) return `${value > 0 ? "+" : ""}${value.toFixed(0)}%`;
    if (key === "roas") return `${value.toFixed(2)}×`;
    if (key.endsWith("_rate")) return `${(value * 100).toFixed(1)}%`;
    if (key === "p_value") return value.toFixed(3);
    return value.toLocaleString("en-IN");
  }
  return String(value);
}
