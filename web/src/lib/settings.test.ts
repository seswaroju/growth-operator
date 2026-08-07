import { describe, expect, it } from "vitest";

import { CAPABILITIES, floorActionLabel, isAuto } from "./settings";

describe("isAuto", () => {
  it("only 'auto' is auto; everything else means Review", () => {
    expect(isAuto("auto")).toBe(true);
    expect(isAuto("draft_only")).toBe(false);
    expect(isAuto("off")).toBe(false);
    expect(isAuto("suggest")).toBe(false);
  });
});

describe("floorActionLabel", () => {
  it("maps the tier-4 money actions to friendly names; humanizes unknown", () => {
    expect(floorActionLabel("payment.refund")).toBe("Issue a refund");
    expect(floorActionLabel("ads.publish")).toBe("Publish an ad");
    expect(floorActionLabel("something.else")).toBe("something else");
  });
});

describe("CAPABILITIES", () => {
  it("covers the three knob capabilities", () => {
    expect(CAPABILITIES.map((c) => c.key)).toEqual(["messaging", "pricing", "campaigns"]);
  });
});
