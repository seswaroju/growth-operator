import { describe, expect, it } from "vitest";

import {
  availableActions, DEFAULT_VARIANTS, MAX_VARIANTS, statusLabel, statusTone, variantBlurb,
} from "./landing";

describe("landing status presentation", () => {
  it("reads 'Live' for a published page", () => {
    expect(statusLabel("published")).toBe("Live");
    expect(statusTone("published")).toBe("good");
  });

  it("flags pages that need the owner", () => {
    expect(statusTone("generated")).toBe("warn");
    expect(statusLabel("generated")).toBe("Ready to review");
  });

  it("falls back to the raw status it does not know", () => {
    expect(statusLabel("weird")).toBe("weird");
    expect(statusTone("weird")).toBe("muted");
  });
});

describe("availableActions mirrors the server transition map", () => {
  it("offers publish once approved or paused", () => {
    expect(availableActions("approved")).toContain("publish");
    expect(availableActions("paused")).toContain("publish");
  });

  it("offers pause only when live, and never publishes a draft", () => {
    expect(availableActions("published")).toEqual(["pause", "archive"]);
    expect(availableActions("generated")).not.toContain("publish");
  });

  it("offers nothing on an archived page", () => {
    expect(availableActions("archived")).toEqual([]);
  });
});

describe("variantBlurb", () => {
  it("describes the known variants and degrades gracefully", () => {
    expect(variantBlurb("focused")).toMatch(/short/i);
    expect(variantBlurb("something-new")).toBe("Alternative layout");
  });
});

describe("variant bounds (LP-4b)", () => {
  it("defaults to 3 layouts and caps at 5", () => {
    expect(DEFAULT_VARIANTS).toBe(3);
    expect(MAX_VARIANTS).toBe(5);
    expect(DEFAULT_VARIANTS).toBeLessThan(MAX_VARIANTS);
  });

  it("describes the extra layouts", () => {
    expect(variantBlurb("catalog")).toMatch(/product-first/i);
    expect(variantBlurb("objection")).toMatch(/doubts/i);
  });
});
