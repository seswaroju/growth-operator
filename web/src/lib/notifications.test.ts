import { describe, expect, it } from "vitest";

import { badge, kindIcon, kindLabel, relativeTime } from "./notifications";

describe("badge", () => {
  it("hides at zero, caps at 9+", () => {
    expect(badge(0)).toBe("");
    expect(badge(3)).toBe("3");
    expect(badge(9)).toBe("9");
    expect(badge(42)).toBe("9+");
  });
});

describe("kind labels/icons", () => {
  it("maps the three kinds", () => {
    expect(kindLabel("approval")).toBe("Approval");
    expect(kindLabel("ticket")).toBe("Support");
    expect(kindLabel("automation")).toBe("Automation");
    expect(kindIcon("approval")).toBe("✅");
  });
});

describe("relativeTime", () => {
  const now = new Date("2026-08-09T12:00:00Z");
  it("formats compact buckets", () => {
    expect(relativeTime("2026-08-09T11:59:40Z", now)).toBe("just now");
    expect(relativeTime("2026-08-09T11:55:00Z", now)).toBe("5m");
    expect(relativeTime("2026-08-09T09:00:00Z", now)).toBe("3h");
    expect(relativeTime("2026-08-07T12:00:00Z", now)).toBe("2d");
  });
});
