import { describe, expect, it } from "vitest";

import type { Lead } from "../api";
import { groupByStage, LEAD_STAGES, STAGE_LABEL } from "./leads";

function lead(stage: string): Lead {
  return {
    id: crypto.randomUUID(), stage, source: "whatsapp", score: null,
    contact_name: "X", contact_phone: null, next_followup_at: null, updated_at: "2026-08-07",
    captured_from: "WhatsApp", landing_page_id: null, landing_slug: null, variant: null,
    channel_type: "whatsapp", utm: {}, recovery_state: "auto",
    recovery_snooze_until: null,
  };
}

describe("groupByStage", () => {
  it("buckets leads into every stage in order (empties included)", () => {
    const grouped = groupByStage([lead("new"), lead("new"), lead("won")]);
    expect(Object.keys(grouped)).toEqual([...LEAD_STAGES]);
    expect(grouped.new).toHaveLength(2);
    expect(grouped.won).toHaveLength(1);
    expect(grouped.lost).toHaveLength(0);
  });
  it("ignores unknown stages rather than crashing", () => {
    const grouped = groupByStage([lead("bogus")]);
    expect(Object.values(grouped).every((v) => v.length === 0)).toBe(true);
  });
});

describe("STAGE_LABEL", () => {
  it("labels every stage", () => {
    for (const s of LEAD_STAGES) expect(STAGE_LABEL[s]).toBeTruthy();
  });
});
