// Cohort benchmarking (OC10) — a store vs its peers, turned into advice. Compares the store's
// metrics against the AVERAGE OF THE OTHER active stores (peer average excludes the store itself),
// derived from the platform rollup sums ÷ peer count. Pure + testable.

import type { AnalyticsRollup, StoreAnalytics } from "../api";

export type Verdict = "ahead" | "behind" | "on_par";

export interface BenchmarkRow {
  key: string;
  label: string;
  money: boolean;
  storeValue: number;
  peerAvg: number;
  deltaPct: number | null; // vs peer average; null when there are no peers
  verdict: Verdict;
}

const BAND = 10; // within ±10% of the peer average = "on par"

type MetricKey = "revenue_minor" | "orders" | "leads" | "quotes";
const METRICS: { key: MetricKey; label: string; money: boolean }[] = [
  { key: "revenue_minor", label: "Revenue", money: true },
  { key: "orders", label: "Orders", money: false },
  { key: "leads", label: "New leads", money: false },
  { key: "quotes", label: "Quotes", money: false },
];

function verdictFor(deltaPct: number | null): Verdict {
  if (deltaPct === null) return "on_par";
  if (deltaPct >= BAND) return "ahead";
  if (deltaPct <= -BAND) return "behind";
  return "on_par";
}

export function benchmark(store: StoreAnalytics, rollup: AnalyticsRollup): BenchmarkRow[] {
  const peers = rollup.active_stores - 1; // exclude this store from its own peer average
  return METRICS.map((m) => {
    const storeValue = store[m.key];
    const peerAvg = peers > 0 ? (rollup[m.key] - storeValue) / peers : 0;
    const deltaPct = peers > 0 && peerAvg > 0
      ? Math.round(((storeValue - peerAvg) / peerAvg) * 100)
      : null;
    return { key: m.key, label: m.label, money: m.money, storeValue, peerAvg, deltaPct,
      verdict: verdictFor(deltaPct) };
  });
}

// The metric where the store trails peers the most — what to advise on. Null if nothing is behind.
export function worstGap(rows: BenchmarkRow[]): BenchmarkRow | null {
  const behind = rows.filter((r) => r.verdict === "behind" && r.deltaPct !== null);
  if (behind.length === 0) return null;
  return behind.reduce((a, b) => (a.deltaPct! <= b.deltaPct! ? a : b));
}

const ADVICE: Record<string, string> = {
  revenue_minor: "Revenue trails peers — focus on converting open quotes and re-engaging warm leads.",
  orders: "Fewer orders than peers — tighten quote follow-up and cut time-to-reply.",
  leads: "Fewer new leads than peers — add top-of-funnel campaigns (WhatsApp / Instagram).",
  quotes: "Fewer quotes than peers — offer a catalog-grounded quote earlier in the chat.",
};

export function advice(row: BenchmarkRow): string {
  return ADVICE[row.key] ?? `${row.label} is below the peer average.`;
}
