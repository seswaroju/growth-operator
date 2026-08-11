import type { AnalyticsRollup, StoreAnalytics } from "../api";
import { rupees } from "../lib/analytics";
import { advice, benchmark, worstGap, type BenchmarkRow } from "../lib/benchmark";
import { tagClasses } from "../lib/ui";
import { Card } from "./ui";

function fmt(r: BenchmarkRow, v: number): string {
  return r.money ? rupees(v) : String(v);
}

// Cohort benchmarking (OC10): the store vs the average of its peers, turned into one line of advice.
export default function StoreBenchmarkCard(
  { store, rollup }: { store: StoreAnalytics; rollup: AnalyticsRollup },
) {
  const rows = benchmark(store, rollup);
  const gap = worstGap(rows);
  const hasPeers = rows.some((r) => r.deltaPct !== null);

  return (
    <Card className="p-5">
      <div className="text-[11px] font-semibold text-muted">
        Benchmarks · vs peers (avg of other stores, last {store.period_days} days)
      </div>
      {!hasPeers ? (
        <p className="mt-2 text-sm text-muted">Not enough peer stores to benchmark yet.</p>
      ) : (
        <>
          <div className="mt-3 space-y-1.5">
            {rows.map((r) => (
              <div key={r.key} className="flex items-center gap-3 text-xs">
                <span className="w-24 shrink-0 text-ink-2">{r.label}</span>
                <span className="w-24 shrink-0 text-right tnum text-ink">{fmt(r, r.storeValue)}</span>
                <span className="w-28 shrink-0 text-right tnum text-muted">
                  peer {fmt(r, Math.round(r.peerAvg))}
                </span>
                <span className="ml-auto">
                  {r.deltaPct === null ? (
                    <span className="text-muted">—</span>
                  ) : (
                    <span className={tagClasses(
                      r.verdict === "ahead" ? "good" : r.verdict === "behind" ? "danger" : "muted")}>
                      {r.deltaPct >= 0 ? "+" : ""}{r.deltaPct}%
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
          {gap && (
            <p className="mt-3 rounded-xl bg-warn-soft px-3 py-2 text-xs text-warn">
              <span className="font-semibold">Advice:</span> {advice(gap)}
            </p>
          )}
        </>
      )}
    </Card>
  );
}
