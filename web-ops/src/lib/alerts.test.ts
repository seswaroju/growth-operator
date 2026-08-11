import { describe, expect, it } from "vitest";

import { buildAlerts, hasDanger } from "./alerts";
import type { OperationalHealth, StoreHealth } from "../api";

function ops(over: Partial<OperationalHealth> = {}): OperationalHealth {
  return {
    outbox_pending: 0, outbox_stuck: 0, approvals_pending: 0, approvals_overdue: 0,
    tickets_open: 0, tickets_urgent: 0, stores_paused: 0, ...over,
  };
}

function store(name: string, band: StoreHealth["churn_band"]): StoreHealth {
  return {
    org_id: name, name, paused: false, open_tickets: 0, urgent_tickets: 0, resolved_7d: 0,
    days_since_activity: 1, revenue_7d: 0, revenue_prev_7d: 0, at_risk: band !== "low",
    churn_score: band === "high" ? 80 : band === "medium" ? 40 : 0, churn_band: band,
    churn_factors: [],
  };
}

describe("buildAlerts (OC9)", () => {
  it("returns nothing when everything is healthy", () => {
    expect(buildAlerts(ops(), [store("A", "low")])).toEqual([]);
  });

  it("raises ops alerts for stuck outbox / overdue approvals / urgent tickets / paused", () => {
    const a = buildAlerts(
      ops({ outbox_stuck: 3, approvals_overdue: 2, tickets_urgent: 1, stores_paused: 4 }), []);
    expect(a.map((x) => x.id).sort()).toEqual(["approvals", "outbox", "paused", "tickets"]);
    expect(a.find((x) => x.id === "outbox")?.detail).toBe("3 undelivered");
  });

  it("raises a churn alert naming the high-risk stores (top 3 + overflow)", () => {
    const health = [
      store("Ratna", "high"), store("Beta", "high"), store("Gamma", "high"), store("Delta", "high"),
      store("Fine", "low"),
    ];
    const churn = buildAlerts(ops(), health).find((x) => x.id === "churn");
    expect(churn?.severity).toBe("danger");
    expect(churn?.detail).toBe("Ratna, Beta, Gamma +1");
  });

  it("sorts danger before warn", () => {
    const a = buildAlerts(ops({ approvals_overdue: 1, outbox_stuck: 1 }), []);
    expect(a[0].severity).toBe("danger"); // outbox
    expect(a[a.length - 1].severity).toBe("warn"); // approvals
  });
});

describe("hasDanger", () => {
  it("is true only when a danger alert is present", () => {
    expect(hasDanger(buildAlerts(ops({ tickets_urgent: 1 }), []))).toBe(false);
    expect(hasDanger(buildAlerts(ops({ outbox_stuck: 1 }), []))).toBe(true);
  });
});
