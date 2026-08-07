import { describe, expect, it } from "vitest";

import { consentLabel, money, orderStatusLabel } from "./customers";

describe("consentLabel", () => {
  it("maps known values; humanizes unknown", () => {
    expect(consentLabel("opted_in")).toBe("Opted in");
    expect(consentLabel("unknown")).toBe("Consent unknown");
    expect(consentLabel("do_not_contact")).toBe("do not contact");
  });
});

describe("orderStatusLabel", () => {
  it("maps the order status set", () => {
    expect(orderStatusLabel("in_progress")).toBe("In progress");
    expect(orderStatusLabel("delivered")).toBe("Delivered");
  });
});

describe("money", () => {
  it("formats minor units; ₹ for INR, code otherwise", () => {
    expect(money(1200000, "INR")).toBe("₹12,000");
    expect(money(5000, "USD")).toBe("USD 50");
  });
});
