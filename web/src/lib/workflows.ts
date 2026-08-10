// Workflow builder — DSL composition (pure, unit-tested), decoupled from the editor UI so a future
// flow-graph editor (DECISIONS 2026-08-09) can target the same model + validate/save API with no
// backend change. The store owner authors a workflow as a linear list of steps; the server is the
// source of truth for validation (this only shapes the DSL — the backend parser validates it).

export type OwnerStepType = "agent_task" | "wait" | "human_task" | "set";

// Steps an owner may author. `emit`/`branch`/`loop` are intentionally excluded from the form: owners
// cannot emit platform events (the backend refuses it), and branch/loop are an advanced follow-on.
export const STEP_TYPES: OwnerStepType[] = ["agent_task", "wait", "human_task", "set"];

export const STEP_LABEL: Record<OwnerStepType, string> = {
  agent_task: "Agent task",
  wait: "Wait",
  human_task: "Human approval",
  set: "Set variables",
};

export type WaitFor = "reply" | "duration" | "event";

export interface StepDraft {
  type: OwnerStepType;
  archetype?: string; // agent_task
  task?: string; // agent_task
  waitFor?: WaitFor; // wait
  timeout?: string; // wait / agent_task ("96h", "2m", …)
  kind?: "approval" | "form"; // human_task
  assignee?: string; // human_task
  varsJson?: string; // set (raw JSON object)
}

export interface WorkflowDraft {
  workflow: string; // key: [a-z0-9_]{3,40}
  version: number;
  eventType: string; // trigger event type
  condition?: string; // optional CEL over payload
  steps: StepDraft[];
}

export function emptyStep(type: OwnerStepType): StepDraft {
  if (type === "wait") return { type, waitFor: "reply", timeout: "96h" };
  if (type === "human_task") return { type, kind: "approval", assignee: "role:owner" };
  if (type === "set") return { type, varsJson: "{}" };
  return { type, archetype: "nurture", task: "" };
}

function parseVars(raw: string | undefined): Record<string, unknown> {
  if (!raw || !raw.trim()) return {};
  try {
    const v = JSON.parse(raw) as unknown;
    return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function stepToDsl(s: StepDraft): Record<string, unknown> {
  switch (s.type) {
    case "agent_task":
      return {
        agent_task: {
          archetype: s.archetype ?? "",
          task: s.task ?? "",
          ...(s.timeout ? { timeout: s.timeout } : {}),
        },
      };
    case "wait":
      return { wait: { for: s.waitFor ?? "reply", ...(s.timeout ? { timeout: s.timeout } : {}) } };
    case "human_task":
      return {
        human_task: {
          kind: s.kind ?? "approval",
          ...(s.assignee ? { assignee: s.assignee } : {}),
        },
      };
    case "set":
      return { set: { vars: parseVars(s.varsJson) } };
  }
}

// Compose the owner's draft into the workflow DSL the backend validates/stores. Pure.
export function composeDsl(draft: WorkflowDraft): Record<string, unknown> {
  const event: Record<string, unknown> = { type: draft.eventType };
  if (draft.condition && draft.condition.trim()) event.condition = draft.condition.trim();
  return {
    workflow: draft.workflow,
    version: draft.version,
    trigger: { event },
    steps: draft.steps.map(stepToDsl),
  };
}

// Client-side hint only (server is the source of truth): a workflow key must be [a-z0-9_]{3,40}.
export function validKey(key: string): boolean {
  return /^[a-z0-9_]{3,40}$/.test(key);
}
