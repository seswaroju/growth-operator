import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  createDefinition,
  listOwnerDefinitions,
  validateDefinition,
  type OwnerDefinition,
  type ValidateResult,
} from "../api";
import { useAuth } from "../auth";
import { hasPermission } from "../lib/roles";
import {
  composeDsl,
  emptyStep,
  STEP_LABEL,
  STEP_TYPES,
  validKey,
  type OwnerStepType,
  type StepDraft,
  type WorkflowDraft,
} from "../lib/workflows";

const input =
  "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none " +
  "focus:border-neutral-900 focus:ring-1 focus:ring-neutral-900";
const btn = "rounded-lg px-3 py-2 text-sm font-medium disabled:opacity-50";

const STATUS_STYLE: Record<string, string> = {
  draft: "bg-neutral-200 text-neutral-700",
  active: "bg-green-100 text-green-800",
  disabled: "bg-amber-100 text-amber-800",
  archived: "bg-neutral-100 text-neutral-500",
};

function emptyDraft(): WorkflowDraft {
  return {
    workflow: "",
    version: 1,
    eventType: "lead.stage.changed",
    condition: "",
    steps: [emptyStep("agent_task")],
  };
}

function fmt(ts: string): string {
  return new Date(ts).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function StepEditor({
  step, onChange, onRemove,
}: {
  step: StepDraft;
  onChange: (s: StepDraft) => void;
  onRemove: () => void;
}) {
  const set = (patch: Partial<StepDraft>) => onChange({ ...step, ...patch });
  return (
    <div className="rounded-lg border border-neutral-200 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <select
          className={input + " max-w-[10rem]"}
          value={step.type}
          onChange={(e) => onChange(emptyStep(e.target.value as OwnerStepType))}
        >
          {STEP_TYPES.map((t) => (
            <option key={t} value={t}>{STEP_LABEL[t]}</option>
          ))}
        </select>
        <button type="button" className="text-xs text-red-600 hover:underline" onClick={onRemove}>
          Remove
        </button>
      </div>
      {step.type === "agent_task" && (
        <div className="grid grid-cols-3 gap-2">
          <input className={input} placeholder="archetype" value={step.archetype ?? ""}
            onChange={(e) => set({ archetype: e.target.value })} />
          <input className={input} placeholder="task" value={step.task ?? ""}
            onChange={(e) => set({ task: e.target.value })} />
          <input className={input} placeholder="timeout (e.g. 2m)" value={step.timeout ?? ""}
            onChange={(e) => set({ timeout: e.target.value })} />
        </div>
      )}
      {step.type === "wait" && (
        <div className="grid grid-cols-2 gap-2">
          <select className={input} value={step.waitFor ?? "reply"}
            onChange={(e) => set({ waitFor: e.target.value as StepDraft["waitFor"] })}>
            <option value="reply">reply</option>
            <option value="duration">duration</option>
            <option value="event">event</option>
          </select>
          <input className={input} placeholder="timeout (e.g. 96h)" value={step.timeout ?? ""}
            onChange={(e) => set({ timeout: e.target.value })} />
        </div>
      )}
      {step.type === "human_task" && (
        <input className={input} placeholder="assignee (e.g. role:owner)" value={step.assignee ?? ""}
          onChange={(e) => set({ assignee: e.target.value })} />
      )}
      {step.type === "set" && (
        <input className={input + " font-mono text-xs"} placeholder='{"key": "value"}'
          value={step.varsJson ?? ""} onChange={(e) => set({ varsJson: e.target.value })} />
      )}
    </div>
  );
}

function Builder({ token }: { token: string }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<WorkflowDraft>(emptyDraft);
  const [check, setCheck] = useState<ValidateResult | null>(null);

  const validate = useMutation({
    mutationFn: () => validateDefinition(token, composeDsl(draft)),
    onSuccess: setCheck,
  });
  const create = useMutation({
    mutationFn: () => createDefinition(token, composeDsl(draft)),
    onSuccess: () => {
      setDraft(emptyDraft());
      setCheck(null);
      qc.invalidateQueries({ queryKey: ["owner-workflows"] });
    },
  });

  const setStep = (i: number, s: StepDraft) =>
    setDraft({ ...draft, steps: draft.steps.map((x, j) => (j === i ? s : x)) });
  const addStep = () => setDraft({ ...draft, steps: [...draft.steps, emptyStep("agent_task")] });
  const removeStep = (i: number) =>
    setDraft({ ...draft, steps: draft.steps.filter((_, j) => j !== i) });

  const keyOk = validKey(draft.workflow);
  const canSubmit = keyOk && draft.eventType.trim().length > 0 && draft.steps.length > 0;

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-4">
      <h3 className="mb-3 text-sm font-semibold">New automation (saved as a draft)</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-neutral-600">
          Key <span className="text-neutral-400">(a-z, 0-9, _)</span>
          <input className={input + (draft.workflow && !keyOk ? " border-red-400" : "")}
            placeholder="reengage_ghost" value={draft.workflow}
            onChange={(e) => setDraft({ ...draft, workflow: e.target.value })} />
        </label>
        <label className="text-xs text-neutral-600">
          Trigger event
          <input className={input} placeholder="lead.stage.changed" value={draft.eventType}
            onChange={(e) => setDraft({ ...draft, eventType: e.target.value })} />
        </label>
        <label className="text-xs text-neutral-600 sm:col-span-2">
          Condition <span className="text-neutral-400">(optional CEL over payload)</span>
          <input className={input + " font-mono text-xs"} placeholder="payload.stage == 'quoted'"
            value={draft.condition ?? ""}
            onChange={(e) => setDraft({ ...draft, condition: e.target.value })} />
        </label>
      </div>

      <p className="mt-3 text-xs text-neutral-500">
        Safety guards (e.g. <code>not_suppressed</code>) are added and locked by the server.
      </p>

      <div className="mt-3 space-y-2">
        {draft.steps.map((s, i) => (
          <StepEditor key={i} step={s} onChange={(x) => setStep(i, x)}
            onRemove={() => removeStep(i)} />
        ))}
        <button type="button" className={btn + " border border-neutral-300"} onClick={addStep}>
          + Add step
        </button>
      </div>

      <div className="mt-4 flex items-center gap-2">
        <button type="button" className={btn + " border border-neutral-300"}
          disabled={!canSubmit || validate.isPending} onClick={() => validate.mutate()}>
          Validate
        </button>
        <button type="button" className={btn + " bg-neutral-900 text-white"}
          disabled={!canSubmit || create.isPending} onClick={() => create.mutate()}>
          {create.isPending ? "Saving…" : "Save draft"}
        </button>
      </div>

      {check && (
        <p className={"mt-3 text-sm " + (check.valid ? "text-green-700" : "text-red-600")}>
          {check.valid
            ? `Valid — guards: ${(check.guards ?? []).join(", ") || "none"}`
            : `Invalid — ${check.error}`}
        </p>
      )}
      {create.isError && (
        <p className="mt-3 text-sm text-red-600">
          {create.error instanceof ApiError ? create.error.message : "Could not save"}
        </p>
      )}
    </div>
  );
}

function List({ token }: { token: string }) {
  const q = useQuery({
    queryKey: ["owner-workflows"],
    queryFn: () => listOwnerDefinitions(token),
  });
  if (q.isLoading) return <p className="text-sm text-neutral-500">Loading…</p>;
  const defs: OwnerDefinition[] = q.data?.definitions ?? [];
  if (defs.length === 0) {
    return <p className="text-sm text-neutral-500">No automations yet — create one below.</p>;
  }
  return (
    <ul className="divide-y divide-neutral-200 rounded-xl border border-neutral-200 bg-white">
      {defs.map((d) => (
        <li key={d.id} className="flex items-center justify-between px-4 py-3 text-sm">
          <div>
            <span className="font-medium">{d.workflow_key}</span>
            <span className="ml-2 text-xs text-neutral-400">v{d.version} · {fmt(d.updated_at)}</span>
          </div>
          <span className={"rounded-full px-2 py-0.5 text-[11px] font-medium " +
            (STATUS_STYLE[d.status] ?? "bg-neutral-100 text-neutral-600")}>
            {d.status}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function WorkflowsSection() {
  const { token, me } = useAuth();
  if (!token) return null;
  if (!hasPermission(me?.roles ?? [], "catalog:write")) {
    return <p className="text-sm text-neutral-500">You don’t have access to automations.</p>;
  }
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">Automations</h2>
        <p className="text-sm text-neutral-500">
          Build your own workflows. New automations save as drafts — activation is reviewed before
          anything runs.
        </p>
      </div>
      <List token={token} />
      <Builder token={token} />
    </div>
  );
}
