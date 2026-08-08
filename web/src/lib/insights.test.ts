import { describe, expect, it } from "vitest";

import { delta, formatValue, metricLabel, OUTCOME_METRICS } from "./insights";

describe("metricLabel", () => {
  it("maps known metrics; humanizes unknown", () => {
    expect(metricLabel("leads_created")).toBe("New inquiries");
    expect(metricLabel("revenue_minor")).toBe("Revenue");
    expect(metricLabel("visits_booked")).toBe("visits booked");
  });
});

describe("formatValue", () => {
  it("revenue is ₹ from minor units; others are counts", () => {
    expect(formatValue("revenue_minor", 180000)).toBe("₹1,800");
    expect(formatValue("orders", 12)).toBe("12");
  });
});

describe("delta", () => {
  it("signs the WoW change; null → em dash", () => {
    expect(delta(100)).toEqual({ text: "+100%", dir: "up" });
    expect(delta(-25)).toEqual({ text: "-25%", dir: "down" });
    expect(delta(0)).toEqual({ text: "0%", dir: "flat" });
    expect(delta(null)).toEqual({ text: "—", dir: "flat" });
  });
});

describe("OUTCOME_METRICS", () => {
  it("are the four owner outcomes", () => {
    expect(OUTCOME_METRICS).toEqual(["leads_created", "quotes_sent", "orders", "revenue_minor"]);
  });
});
