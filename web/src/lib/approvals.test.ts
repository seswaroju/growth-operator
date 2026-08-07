import { describe, expect, it } from "vitest";

import { actionLabel, draftText, expiryLabel, priceLabel, tierLabel } from "./approvals";

describe("actionLabel", () => {
  it("plain reply vs priced quote for messages.send", () => {
    expect(actionLabel({ action_type: "messages.send", payload: { body: "hi" } }))
      .toBe("Reply to customer");
    expect(actionLabel({ action_type: "messages.send", payload: { amount_minor: 5000 } }))
      .toBe("Send quote");
  });
  it("known tools get friendly labels; unknown falls back to the tool name", () => {
    expect(actionLabel({ action_type: "campaigns.execute", payload: {} })).toBe("Send campaign");
    expect(actionLabel({ action_type: "catalog.write", payload: {} })).toBe("Update catalog");
    expect(actionLabel({ action_type: "widgets.frobnicate", payload: {} }))
      .toBe("widgets.frobnicate");
  });
});

describe("draftText", () => {
  it("reads body/text/message; empty when absent or non-string", () => {
    expect(draftText({ body: "Namaste" })).toBe("Namaste");
    expect(draftText({ text: "hi" })).toBe("hi");
    expect(draftText({})).toBe("");
    expect(draftText({ body: 42 })).toBe("");
  });
});

describe("priceLabel", () => {
  it("formats amount_minor as rupees; null when not priced", () => {
    expect(priceLabel({ amount_minor: 180000 })).toBe("₹1,800");
    expect(priceLabel({})).toBe(null);
    expect(priceLabel({ amount_minor: "180000" })).toBe(null);
  });
});

describe("tierLabel", () => {
  it("tier 3+ is high-stakes", () => {
    expect(tierLabel(2)).toBe("Needs your OK");
    expect(tierLabel(3)).toBe("High-stakes");
  });
});

describe("expiryLabel", () => {
  const now = Date.parse("2026-08-07T12:00:00Z");
  it("minutes, hours, and expired", () => {
    expect(expiryLabel("2026-08-07T12:30:00Z", now)).toBe("expires in 30 min");
    expect(expiryLabel("2026-08-07T14:30:00Z", now)).toBe("expires in 2h 30m");
    expect(expiryLabel("2026-08-07T14:00:00Z", now)).toBe("expires in 2h");
    expect(expiryLabel("2026-08-07T11:30:00Z", now)).toBe("expired");
  });
});
