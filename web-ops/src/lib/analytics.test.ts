import { describe, expect, it } from "vitest";

import { rupees, wowDelta } from "./analytics";

describe("wowDelta (OC4)", () => {
  it("computes up/down/flat percentages", () => {
    expect(wowDelta(120, 100)).toEqual({ text: "+20%", dir: "up" });
    expect(wowDelta(80, 100)).toEqual({ text: "-20%", dir: "down" });
    expect(wowDelta(100, 100)).toEqual({ text: "0%", dir: "flat" });
  });

  it("handles a zero prior window", () => {
    expect(wowDelta(50, 0)).toEqual({ text: "new", dir: "flat" });
    expect(wowDelta(0, 0)).toEqual({ text: "—", dir: "flat" });
  });

  it("rupees formats minor units", () => {
    expect(rupees(2_500_000)).toBe("₹25,000");
    expect(rupees(0)).toBe("₹0");
  });
});
