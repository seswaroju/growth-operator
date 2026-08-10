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
import { buttonClasses, fieldClasses } from "../lib/ui";
import { Bolt, Plus } from "./icons";
import { Card, EmptyState, PageHeader } from "./ui";

const input = fieldClasses("w-full");

const STATUS_TONE: Record<string, string> = {
  draft: "bg-line-2 text-ink-2",
  active: "bg-good-soft text-good",
  disabled: "bg-warn-soft text-warn",
  archived: "bg-line-2 text-ink-2",
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
    <div className="rounded-xl border border-line bg-raised p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <select
          className={fieldClasses("max-w-[10rem]")}
          value={step.type}
          onChange={(e) => onChange(emptyStep(e.target.value as OwnerStepType))}
        >
          {STEP_TYPES.map((t) => (
            <option key={t} value={t}>{STEP_LABEL[t]}</option>
          ))}
        </select>
        <button type="button" className="text-xs font-medium text-danger hover:underline" onClick={onRemove}>
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
        <input className={fieldClasses("w-full font-mono text-xs")} placeholder='{"key": "value"}'
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
  const lbl = "text-xs font-medium text-ink-2";

  return (
    <Card className="p-4">
      <h3 className="mb-3 text-sm font-semibold">New automation (saved as a draft)</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className={lbl}>
          Key <span className="text-muted">(a-z, 0-9, _)</span>
          <input className={draft.workflow && !keyOk ? fieldClasses("mt-1.5 w-full border-danger") : fieldClasses("mt-1.5 w-full")}
            placeholder="reengage_quiet" value={draft.workflow}
            onChange={(e) => setDraft({ ...draft, workflow: e.target.value })} />
        </label>
        <label className={lbl}>
          Trigger event
          <input className={fieldClasses("mt-1.5 w-full")} placeholder="lead.stage.changed" value={draft.eventType}
            onChange={(e) => setDraft({ ...draft, eventType: e.target.value })} />
        </label>
        <label className={`${lbl} sm:col-span-2`}>
          Condition <span className="text-muted">(optional CEL over payload)</span>
          <input className={fieldClasses("mt-1.5 w-full font-mono text-xs")} placeholder="payload.stage == 'quoted'"
            value={draft.condition ?? ""}
            onChange={(e) => setDraft({ ...draft, condition: e.target.value })} />
        </label>
      </div>

      <p className="mt-3 text-xs text-muted">
        Safety guards (e.g. <code className="rounded bg-line-2 px-1 py-0.5 font-mono text-[11px] text-ink-2">not_suppressed</code>)
        are added and locked by the server.
      </p>

      <div className="mt-3 space-y-2">
        {draft.steps.map((s, i) => (
          <StepEditor key={i} step={s} onChange={(x) => setStep(i, x)}
            onRemove={() => removeStep(i)} />
        ))}
        <button type="button" className={buttonClasses("ghost", "sm")} onClick={addStep}>
          <Plus className="h-[15px] w-[15px]" />
          Add step
        </button>
      </div>

      <div className="mt-4 flex items-center gap-2">
        <button type="button" className={buttonClasses("ghost", "md")}
          disabled={!canSubmit || validate.isPending} onClick={() => validate.mutate()}>
          Validate
        </button>
        <button type="button" className={buttonClasses("primary", "md")}
          disabled={!canSubmit || create.isPending} onClick={() => create.mutate()}>
          {create.isPending ? "Saving…" : "Save draft"}
        </button>
      </div>

      {check && (
        <p className={"mt-3 text-sm " + (check.valid ? "text-good" : "text-danger")}>
          {check.valid
            ? `Valid — guards: ${(check.guards ?? []).join(", ") || "none"}`
            : `Invalid — ${check.error}`}
        </p>
      )}
      {create.isError && (
        <p className="mt-3 text-sm text-danger">
          {create.error instanceof ApiError ? create.error.message : "Could not save"}
        </p>
      )}
    </Card>
  );
}

function List({ token }: { token: string }) {
  const q = useQuery({
    queryKey: ["owner-workflows"],
    queryFn: () => listOwnerDefinitions(token),
  });
  if (q.isLoading) return <p className="text-sm text-muted">Loading…</p>;
  const defs: OwnerDefinition[] = q.data?.definitions ?? [];
  if (defs.length === 0) {
    return (
      <Card>
        <EmptyState
          icon={<Bolt className="h-6 w-6" />}
          title="No automations yet"
          hint="Build one below — new automations save as drafts and are reviewed before anything runs."
        />
      </Card>
    );
  }
  return (
    <Card>
      <ul className="divide-y divide-line-2">
        {defs.map((d) => (
          <li key={d.id} className="flex items-center justify-between px-4 py-3 text-sm">
            <div>
              <span className="font-semibold text-ink">{d.workflow_key}</span>
              <span className="ml-2 text-xs text-muted">v{d.version} · {fmt(d.updated_at)}</span>
            </div>
            <span className={"inline-flex items-center rounded-lg px-2.5 py-1 text-[11px] font-semibold " +
              (STATUS_TONE[d.status] ?? "bg-line-2 text-ink-2")}>
              {d.status}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export default function WorkflowsSection() {
  const { token, me } = useAuth();
  if (!token) return null;
  if (!hasPermission(me?.roles ?? [], "catalog:write")) {
    return <p className="text-sm text-muted">You don't have access to automations.</p>;
  }
  return (
    <div>
      <PageHeader
        title="Automations"
        subtitle="Build your own workflows. New automations save as drafts — activation is reviewed before anything runs."
      />
      <div className="space-y-4">
        <List token={token} />
        <Builder token={token} />
      </div>
    </div>
  );
}
