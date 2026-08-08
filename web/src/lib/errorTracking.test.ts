import { describe, expect, it } from "vitest";

import { initErrorTracking, scrubDeep, scrubText } from "./errorTracking";

describe("scrubText", () => {
  it("masks phone, OTP, and email", () => {
    const out = scrubText("call +919876543210 code 123456 mail riya@example.com");
    expect(out).not.toContain("+919876543210");
    expect(out).toContain("[redacted-phone]");
    expect(out).not.toContain("123456");
    expect(out).toContain("[redacted-otp]");
    expect(out).not.toContain("riya@example.com");
    expect(out).toContain("[redacted-email]");
  });
});

describe("scrubDeep", () => {
  it("drops sensitive keys and scrubs nested strings", () => {
    const event = {
      message: "boom for riya@example.com",
      extra: { otp: "998877", authorization: "Bearer x", note: "call +14155551234" },
      list: [{ password: "hunter2" }, "plain +14155550000"],
    };
    const out = scrubDeep(event) as typeof event;
    expect(out.extra.otp).toBe("[redacted]");
    expect(out.extra.authorization).toBe("[redacted]");
    expect((out.list[0] as { password: string }).password).toBe("[redacted]");
    expect(out.message).not.toContain("riya@example.com");
    expect(out.extra.note).toContain("[redacted-phone]");
    expect(out.list[1]).toContain("[redacted-phone]");
  });
});

describe("initErrorTracking", () => {
  it("is inert (returns false) when no VITE_ERROR_DSN is configured", () => {
    // In the test env VITE_ERROR_DSN is unset → nothing is initialized, nothing leaves the browser.
    expect(initErrorTracking()).toBe(false);
  });
});
