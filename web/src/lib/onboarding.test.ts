import { describe, expect, it } from "vitest";

import { isComplete, onboardingSteps, progress } from "./onboarding";
import type { Onboarding } from "../api";

function status(o: Partial<Onboarding>): Onboarding {
  return { whatsapp_connected: false, catalog_items: 0, campaigns: 0, team_members: 1, ...o };
}

describe("onboardingSteps (OC11)", () => {
  it("marks each step done from the right signal", () => {
    const steps = onboardingSteps(status({
      whatsapp_connected: true, catalog_items: 3, campaigns: 1, team_members: 2,
    }));
    expect(Object.fromEntries(steps.map((s) => [s.key, s.done]))).toEqual({
      whatsapp: true, catalog: true, team: true, campaign: true,
    });
  });

  it("team needs more than the owner alone; catalog/campaign need at least one", () => {
    const steps = onboardingSteps(status({ team_members: 1, catalog_items: 0, campaigns: 0 }));
    const done = Object.fromEntries(steps.map((s) => [s.key, s.done]));
    expect(done.team).toBe(false);     // owner only
    expect(done.catalog).toBe(false);
    expect(done.campaign).toBe(false);
  });
});

describe("progress / isComplete", () => {
  it("counts completed steps and a percentage", () => {
    const steps = onboardingSteps(status({ whatsapp_connected: true, catalog_items: 5 }));
    expect(progress(steps)).toEqual({ completed: 2, total: 4, pct: 50 });
    expect(isComplete(steps)).toBe(false);
  });

  it("isComplete only when every step is done", () => {
    const all = onboardingSteps(status({
      whatsapp_connected: true, catalog_items: 1, campaigns: 1, team_members: 2,
    }));
    expect(isComplete(all)).toBe(true);
    expect(progress(all).pct).toBe(100);
  });
});
