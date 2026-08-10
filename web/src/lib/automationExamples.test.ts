import { describe, expect, it } from "vitest";

import { AUTOMATION_EXAMPLES, AUTOMATION_OPTIONS } from "./automationExamples";
import { composeDsl, STEP_TYPES, validKey } from "./workflows";

describe("automation examples (TX1)", () => {
  it("covers the requested spread: >=2 simple, >=2 medium, >=3 complex", () => {
    const by = (c: string) => AUTOMATION_EXAMPLES.filter((e) => e.complexity === c).length;
    expect(by("simple")).toBeGreaterThanOrEqual(2);
    expect(by("medium")).toBeGreaterThanOrEqual(2);
    expect(by("complex")).toBeGreaterThanOrEqual(3);
  });

  it("every example is a valid, editable draft the builder accepts", () => {
    for (const ex of AUTOMATION_EXAMPLES) {
      expect(validKey(ex.draft.workflow), ex.id).toBe(true);
      expect(ex.draft.steps.length, ex.id).toBeGreaterThan(0);
      for (const s of ex.draft.steps) {
        expect(STEP_TYPES, `${ex.id}: ${s.type}`).toContain(s.type);
      }
      // set-steps carry parseable JSON
      for (const s of ex.draft.steps) {
        if (s.type === "set") expect(() => JSON.parse(s.varsJson ?? "{}")).not.toThrow();
      }
      // composes to a DSL with a trigger + one instruction per step
      const dsl = composeDsl(ex.draft) as { trigger: unknown; steps: unknown[] };
      expect(dsl.trigger, ex.id).toBeTruthy();
      expect(dsl.steps.length, ex.id).toBe(ex.draft.steps.length);
    }
  });

  it("ids and workflow keys are unique", () => {
    expect(new Set(AUTOMATION_EXAMPLES.map((e) => e.id)).size).toBe(AUTOMATION_EXAMPLES.length);
    expect(new Set(AUTOMATION_EXAMPLES.map((e) => e.draft.workflow)).size)
      .toBe(AUTOMATION_EXAMPLES.length);
  });

  it("every option doc has what / why / how", () => {
    expect(AUTOMATION_OPTIONS.length).toBeGreaterThanOrEqual(6);
    for (const o of AUTOMATION_OPTIONS) {
      expect(o.name && o.what && o.why && o.how, o.name).toBeTruthy();
    }
  });
});
