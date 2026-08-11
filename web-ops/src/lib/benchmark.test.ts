import { describe, expect, it } from "vitest";

import { advice, benchmark, worstGap } from "./benchmark";
import type { AnalyticsRollup, StoreAnalytics } from "../api";

function store(o: Partial<StoreAnalytics>): StoreAnalytics {
  return {
    period_days: 30, revenue_minor: 0, revenue_minor_prev: 0, orders: 0, orders_prev: 0,
    leads: 0, leads_prev: 0, quotes: 0, quotes_prev: 0, campaigns_run: 0, messages_sent: 0,
    campaigns_analyzed: 0, attributed_revenue_minor: 0, ...o,
  };
}

function rollup(o: Partial<AnalyticsRollup>): AnalyticsRollup {
  return {
    period_days: 30, revenue_minor: 0, revenue_minor_prev: 0, orders: 0, orders_prev: 0,
    leads: 0, leads_prev: 0, quotes: 0, quotes_prev: 0, active_stores: 0, campaigns_run: 0,
    messages_sent: 0, campaigns_analyzed: 0, attributed_revenue_minor: 0, ...o,
  };
}

describe("benchmark (OC10)", () => {
  it("compares the store to the average of OTHER active stores", () => {
    const rows = benchmark(
      store({ revenue_minor: 1_000_000, orders: 10, leads: 20, quotes: 5 }),
      rollup({ active_stores: 5, revenue_minor: 6_000_000, orders: 60, leads: 40, quotes: 30 }),
    );
    const byKey = Object.fromEntries(rows.map((r) => [r.key, r]));
    // revenue: peers=(6M-1M)/4=1.25M → (1M-1.25M)/1.25M = -20% behind
    expect(byKey.revenue_minor.peerAvg).toBe(1_250_000);
    expect(byKey.revenue_minor.deltaPct).toBe(-20);
    expect(byKey.revenue_minor.verdict).toBe("behind");
    // leads: peers=(40-20)/4=5 → (20-5)/5 = +300% ahead
    expect(byKey.leads.deltaPct).toBe(300);
    expect(byKey.leads.verdict).toBe("ahead");
  });

  it("treats ±10% as the on_par band boundary", () => {
    // peers = (31-11)/2 = 10 → +10% exactly → ahead (>= band)
    const edge = benchmark(store({ orders: 11 }), rollup({ active_stores: 3, orders: 31 }));
    expect(edge.find((r) => r.key === "orders")?.verdict).toBe("ahead");
    // peers = (305-105)/2 = 100 → +5% → on_par
    const near = benchmark(store({ orders: 105 }), rollup({ active_stores: 3, orders: 305 }));
    expect(near.find((r) => r.key === "orders")?.verdict).toBe("on_par");
  });

  it("has no peers when it's the only active store → deltaPct null, on_par", () => {
    const rows = benchmark(store({ revenue_minor: 500 }), rollup({ active_stores: 1, revenue_minor: 500 }));
    expect(rows.every((r) => r.deltaPct === null && r.verdict === "on_par")).toBe(true);
  });

  it("worstGap picks the most-behind metric; advice explains it", () => {
    const rows = benchmark(
      store({ revenue_minor: 900_000, orders: 5, leads: 1, quotes: 4 }),
      rollup({ active_stores: 3, revenue_minor: 900_000 + 2_000_000, orders: 5 + 20,
        leads: 1 + 20, quotes: 4 + 10 }),
    );
    const gap = worstGap(rows);
    expect(gap?.key).toBe("leads"); // leads most below peers
    expect(advice(gap!)).toContain("top-of-funnel");
  });

  it("worstGap is null when nothing is behind", () => {
    const rows = benchmark(
      store({ revenue_minor: 5_000_000, orders: 100 }),
      rollup({ active_stores: 3, revenue_minor: 5_000_000 + 100, orders: 100 + 2 }),
    );
    expect(worstGap(rows)).toBeNull();
  });
});
