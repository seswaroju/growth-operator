import { describe, expect, it } from "vitest";

import { composeDsl, emptyStep, validKey, type WorkflowDraft } from "./workflows";

const base: WorkflowDraft = {
  workflow: "reengage_ghost",
  version: 1,
  eventType: "lead.stage.changed",
  condition: "payload.stage == 'quoted'",
  steps: [
    { type: "agent_task", archetype: "nurture", task: "diagnose", timeout: "2m" },
    { type: "wait", waitFor: "reply", timeout: "96h" },
    { type: "human_task", kind: "approval", assignee: "role:owner" },
    { type: "set", varsJson: '{"flagged": true}' },
  ],
};

describe("composeDsl", () => {
  it("shapes the trigger with an optional condition", () => {
    const dsl = composeDsl(base);
    expect(dsl.workflow).toBe("reengage_ghost");
    expect(dsl.version).toBe(1);
    expect(dsl.trigger).toEqual({
      event: { type: "lead.stage.changed", condition: "payload.stage == 'quoted'" },
    });
  });

  it("omits the condition when blank", () => {
    const dsl = composeDsl({ ...base, condition: "  " });
    expect(dsl.trigger).toEqual({ event: { type: "lead.stage.changed" } });
  });

  it("maps each owner step type to its DSL verb", () => {
    const steps = composeDsl(base).steps as Record<string, unknown>[];
    expect(steps[0]).toEqual({ agent_task: { archetype: "nurture", task: "diagnose", timeout: "2m" } });
    expect(steps[1]).toEqual({ wait: { for: "reply", timeout: "96h" } });
    expect(steps[2]).toEqual({ human_task: { kind: "approval", assignee: "role:owner" } });
    expect(steps[3]).toEqual({ set: { vars: { flagged: true } } });
  });

  it("falls back to empty vars on invalid JSON", () => {
    const steps = composeDsl({ ...base, steps: [{ type: "set", varsJson: "not json" }] }).steps as
      Record<string, unknown>[];
    expect(steps[0]).toEqual({ set: { vars: {} } });
  });
});

describe("emptyStep", () => {
  it("gives sensible defaults per type", () => {
    expect(emptyStep("wait")).toEqual({ type: "wait", waitFor: "reply", timeout: "96h" });
    expect(emptyStep("human_task").kind).toBe("approval");
    expect(emptyStep("agent_task").archetype).toBe("nurture");
  });
});

describe("validKey", () => {
  it("accepts snake_case keys and rejects the rest", () => {
    expect(validKey("reengage_ghost")).toBe(true);
    expect(validKey("ab")).toBe(false); // too short
    expect(validKey("Bad-Key")).toBe(false); // uppercase + dash
  });
});
