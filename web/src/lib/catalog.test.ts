import { describe, expect, it } from "vitest";

import { availabilityLabel, priceLabel, rupeesToMinor } from "./catalog";

describe("priceLabel", () => {
  it("static shows the base price; computed shows a live-rate badge", () => {
    expect(priceLabel({ price_mode: "static", base_price_minor: 180000, currency: "INR" }))
      .toBe("₹1,800");
    expect(priceLabel({ price_mode: "computed", base_price_minor: null, currency: "INR" }))
      .toBe("Live rate");
    expect(priceLabel({ price_mode: "static", base_price_minor: null, currency: "INR" }))
      .toBe("—");
  });
  it("non-INR currency keeps its code", () => {
    expect(priceLabel({ price_mode: "static", base_price_minor: 5000, currency: "USD" }))
      .toBe("USD 50");
  });
});

describe("availabilityLabel", () => {
  it("maps known values; humanizes unknown", () => {
    expect(availabilityLabel("in_stock")).toBe("In stock");
    expect(availabilityLabel("made_to_order")).toBe("Made to order");
    expect(availabilityLabel("back_order")).toBe("back order");
  });
});

describe("rupeesToMinor", () => {
  it("converts rupees to minor units; null on non-numeric", () => {
    expect(rupeesToMinor("1800")).toBe(180000);
    expect(rupeesToMinor("1800.50")).toBe(180050);
    expect(rupeesToMinor("")).toBe(null);
    expect(rupeesToMinor("abc")).toBe(null);
  });
});
