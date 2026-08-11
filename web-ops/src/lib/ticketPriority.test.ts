import { describe, expect, it } from "vitest";

import {
  plansByTier, rankTickets, slaBoard, slaBucket, slaHoursForTier, slaStatus, slaTargets, tierRank,
} from "./ticketPriority";
import type { AdminTicket, BillingPlan, TicketPriority, TicketSeverity, TicketStatus } from "../api";

function plan(name: string, price: number): BillingPlan {
  return {
    id: name, name, price_minor: price, active: true, description: null, features: [],
    max_managers: 0, max_staff: 0, config: {}, created_at: "",
  };
}

function ticket(o: {
  id: string; org_id: string; status?: TicketStatus; priority?: TicketPriority;
  severity?: TicketSeverity; created_at?: string;
}): AdminTicket {
  return {
    id: o.id, org_id: o.org_id, org_name: o.org_id, raised_by: null, subject: o.id, description: "",
    category: "account", priority: o.priority ?? "normal", severity: o.severity ?? "minor",
    status: o.status ?? "open", resolution_note: null, created_at: o.created_at ?? "2026-08-10T00:00:00Z",
    updated_at: "", resolved_at: null,
  };
}

const HOUR = 3_600_000;

describe("ticket priority + SLA (OC3)", () => {
  it("plansByTier orders by price desc; tierRank finds/falls back", () => {
    const order = plansByTier([plan("Base", 100), plan("Growth", 500), plan("Pro", 900)]);
    expect(order).toEqual(["Pro", "Growth", "Base"]);
    expect(tierRank("Pro", order)).toBe(0);
    expect(tierRank("Base", order)).toBe(2);
    expect(tierRank(null, order)).toBe(3); // no plan → worst
    expect(tierRank("Unknown", order)).toBe(3);
  });

  it("slaHoursForTier clamps past the table end", () => {
    expect(slaHoursForTier(0)).toBe(4);
    expect(slaHoursForTier(2)).toBe(24);
    expect(slaHoursForTier(99)).toBe(48); // last value
  });

  it("slaStatus flags breach at the boundary", () => {
    const created = "2026-08-10T00:00:00Z";
    const base = new Date(created).getTime();
    const within = slaStatus(created, 4, base + 3 * HOUR);
    expect(within.breached).toBe(false);
    expect(within.label).toBe("1h left");
    const over = slaStatus(created, 4, base + 6 * HOUR);
    expect(over.breached).toBe(true);
    expect(over.label).toBe("overdue 2h");
  });

  it("ranks: open before closed, breached first, then higher tier, then urgency", () => {
    const order = plansByTier([plan("Base", 100), plan("Pro", 900)]);
    const orgPlan = new Map<string, string | null>([
      ["pro", "Pro"], ["base", "Base"], ["none", null],
    ]);
    const now = new Date("2026-08-10T10:00:00Z").getTime();
    const old = "2026-08-10T00:00:00Z"; // 10h ago
    const fresh = "2026-08-10T09:30:00Z"; // 30m ago
    const tickets: AdminTicket[] = [
      ticket({ id: "resolved", org_id: "pro", status: "resolved", created_at: old }),
      ticket({ id: "base-fresh", org_id: "base", created_at: fresh }),
      ticket({ id: "pro-breached", org_id: "pro", created_at: old }), // Pro SLA 4h, 10h old → breach
      ticket({ id: "none-fresh", org_id: "none", created_at: fresh }),
    ];
    const ranked = rankTickets(tickets, orgPlan, order, now);
    const ids = ranked.map((r) => r.ticket.id);
    // breached Pro first, then non-breached by tier (base before none), resolved last
    expect(ids).toEqual(["pro-breached", "base-fresh", "none-fresh", "resolved"]);
    expect(ranked[0].sla?.breached).toBe(true);
    expect(ranked[3].open).toBe(false);
  });
});

describe("SLA board (OC8)", () => {
  const order = plansByTier([plan("Pro", 900)]); // Pro → SLA 4h; no-plan fallback → 8h
  const orgPlan = new Map<string, string | null>([["pro", "Pro"]]);
  const now = new Date("2026-08-10T12:00:00Z").getTime();
  const ago = (h: number) => new Date(now - h * HOUR).toISOString();
  const ranked = (items: AdminTicket[]) => rankTickets(items, orgPlan, order, now);

  it("buckets open tickets: breached / about-to-breach / on-track; closed excluded", () => {
    const r = ranked([
      ticket({ id: "breached", org_id: "pro", created_at: ago(10) }), // 4h SLA, 10h old
      ticket({ id: "atrisk", org_id: "pro", created_at: ago(3.5) }),  // 0.5h left <= 1h (25%)
      ticket({ id: "ontrack", org_id: "pro", created_at: ago(1) }),   // 3h left
      ticket({ id: "closed", org_id: "pro", status: "resolved", created_at: ago(10) }),
    ]);
    const byId = Object.fromEntries(r.map((x) => [x.ticket.id, x]));
    expect(slaBucket(byId.breached)).toBe("breached");
    expect(slaBucket(byId.atrisk)).toBe("at_risk");
    expect(slaBucket(byId.ontrack)).toBe("on_track");
    expect(slaBucket(byId.closed)).toBeNull();

    const board = slaBoard(r);
    expect(board.breached.map((x) => x.ticket.id)).toEqual(["breached"]);
    expect(board.at_risk.map((x) => x.ticket.id)).toEqual(["atrisk"]);
    expect(board.on_track.map((x) => x.ticket.id)).toEqual(["ontrack"]);
  });

  it("slaTargets lists the response target per tier + a no-plan fallback", () => {
    expect(slaTargets(order)).toEqual([
      { plan: "Pro", hours: 4 },
      { plan: "no plan", hours: 8 },
    ]);
  });

  it("atRiskFraction is configurable", () => {
    const r = ranked([ticket({ id: "t", org_id: "pro", created_at: ago(2) })]); // 2h left = 50%
    expect(slaBucket(r[0])).toBe("on_track");     // default 25%
    expect(slaBucket(r[0], 0.5)).toBe("at_risk"); // 50%: 2h <= 2h
  });
});
