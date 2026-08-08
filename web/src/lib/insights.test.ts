import { describe, expect, it } from "vitest";

import {
  confidenceTone, delta, driverTone, formatBreakdownValue, formatValue, humanizeBreakdownKey,
  metricLabel, OUTCOME_METRICS, QUESTION_LEVELS, reportTypeLabel,
} from "./insights";

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

describe("QUESTION_LEVELS", () => {
  it("escalate through the four record layers in order", () => {
    expect(QUESTION_LEVELS.map((q) => q.level)).toEqual([1, 2, 3, 4]);
    expect(QUESTION_LEVELS.map((q) => q.layer)).toEqual([
      "verdict", "drivers", "full_breakdown", "evidence",
    ]);
  });
});

describe("reportTypeLabel", () => {
  it("maps known report types; humanizes unknown", () => {
    expect(reportTypeLabel("campaign_analysis")).toBe("Campaign analysis");
    expect(reportTypeLabel("marketing_strategy")).toBe("Marketing strategy");
    expect(reportTypeLabel("seo_audit")).toBe("seo audit");
  });
});

describe("driverTone / confidenceTone", () => {
  it("driver surfaces the engine's flag; unknown → neutral", () => {
    expect(driverTone("good")).toBe("good");
    expect(driverTone("bad")).toBe("bad");
    expect(driverTone("meh")).toBe("neutral");
  });
  it("confidence: high→good, low→bad, medium/null→neutral", () => {
    expect(confidenceTone("high")).toBe("good");
    expect(confidenceTone("low")).toBe("bad");
    expect(confidenceTone("medium")).toBe("neutral");
    expect(confidenceTone(null)).toBe("neutral");
  });
});

describe("humanizeBreakdownKey", () => {
  it("uses the label map, strips _minor, humanizes the rest", () => {
    expect(humanizeBreakdownKey("roas")).toBe("ROAS");
    expect(humanizeBreakdownKey("revenue_minor")).toBe("Revenue");
    expect(humanizeBreakdownKey("net_minor")).toBe("Net result");
    expect(humanizeBreakdownKey("some_other_key")).toBe("some other key");
  });
});

describe("formatBreakdownValue", () => {
  it("formats by key convention: money, %, ROAS, rate, p-value, bool", () => {
    expect(formatBreakdownValue("revenue_minor", 180000)).toBe("₹1,800");
    expect(formatBreakdownValue("roi_pct", 42)).toBe("+42%");
    expect(formatBreakdownValue("lift_pct", -10)).toBe("-10%");
    expect(formatBreakdownValue("roas", 2.5)).toBe("2.50×");
    expect(formatBreakdownValue("campaign_rate", 0.123)).toBe("12.3%");
    expect(formatBreakdownValue("p_value", 0.0123)).toBe("0.012");
    expect(formatBreakdownValue("is_significant", true)).toBe("Yes");
    expect(formatBreakdownValue("window_days", 30)).toBe("30");
    expect(formatBreakdownValue("anything", null)).toBe("—");
  });
});
