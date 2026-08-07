import { describe, expect, it } from "vitest";

import { isFromStore, preview, senderLabel } from "./conversations";

describe("direction helpers", () => {
  it("outbound is from the store; anything else is the customer", () => {
    expect(isFromStore("outbound")).toBe(true);
    expect(isFromStore("inbound")).toBe(false);
    expect(isFromStore(null)).toBe(false);
    expect(senderLabel("outbound")).toBe("You");
    expect(senderLabel("inbound")).toBe("Customer");
  });
});

describe("preview", () => {
  it("collapses whitespace, truncates, and handles empty", () => {
    expect(preview("Hello   there\nworld")).toBe("Hello there world");
    expect(preview(null)).toBe("No messages yet");
    expect(preview("x".repeat(100), 10)).toBe("xxxxxxxxx…");
  });
});
