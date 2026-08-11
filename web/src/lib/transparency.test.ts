import { describe, expect, it } from "vitest";

import { channelLabel, monthLabel, roasLabel, spendShare } from "./transparency";

describe("channelLabel", () => {
  it("maps known channels and humanizes unknown ones", () => {
    expect(channelLabel("whatsapp")).toBe("WhatsApp");
    expect(channelLabel("google_ads")).toBe("Google Ads");
    expect(channelLabel("subscription")).toBe("Subscription");
    expect(channelLabel("mystery")).toBe("Mystery");
  });
});

describe("roasLabel", () => {
  it("formats a multiple to two decimals, em dash when null", () => {
    expect(roasLabel(1.05)).toBe("1.05×");
    expect(roasLabel(2)).toBe("2.00×");
    expect(roasLabel(null)).toBe("—");
  });
});

describe("spendShare", () => {
  it("is the fraction of total, and 0 when there is no spend", () => {
    expect(spendShare(2_500_000, 10_000_000)).toBe(0.25);
    expect(spendShare(5_000, 0)).toBe(0);
  });
});

describe("monthLabel", () => {
  it("renders a friendly month, falling back on garbage", () => {
    expect(monthLabel("2026-08")).toBe("August 2026");
    expect(monthLabel("nonsense")).toBe("nonsense");
  });
});
