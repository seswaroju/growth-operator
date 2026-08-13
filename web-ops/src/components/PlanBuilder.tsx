// Operator Plan Builder (PLAN-4).
//
// Authoring happens against the canonical catalog rather than a textarea: the operator picks from
// what the product can actually sell, and the server validates the same rules again. Nothing here
// is an authorization boundary — the preview says what a plan *grants*, while whether an individual
// route is gated is still PLAN-5's job, which the preview states in its own assumptions.
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  adminPlanCatalog, adminPreviewPlan, adminUpdatePlanStructured,
  type BillingPlan, type CatalogCapability, type PlanConfig, type PlanDraft,
} from "../api";
import { buttonClasses } from "../lib/ui";
import { Card } from "./ui";

interface Props {
  token: string;
  plan: BillingPlan;
  onDone: () => void;
}

function cfgOf(plan: BillingPlan): PlanConfig {
  const c = plan.config ?? {};
  return {
    entitlement_schema_version: 1,
    entitlements: c.entitlements ?? [],
    agents: c.agents ?? [],
    channels: c.channels ?? [],
    addons: c.addons ?? [],
    promotions: c.promotions ?? [],
    vertical: c.vertical ?? null,
    display: c.display ?? { bullets: [] },
  };
}

function Chip({ text, tone }: { text: string; tone?: "warn" | "ok" }) {
  const cls = tone === "warn"
    ? "bg-line-2 text-ink-2"
    : tone === "ok" ? "bg-line-2 text-ink" : "bg-line-2 text-muted";
  return <span className={`rounded-lg ${cls} px-1.5 py-0.5 text-[10px] font-semibold`}>{text}</span>;
}

function toggle(list: string[], key: string): string[] {
  return list.includes(key) ? list.filter((k) => k !== key) : [...list, key].sort();
}

export function PlanBuilder({ token, plan, onDone }: Props) {
  const qc = useQueryClient();
  const [name, setName] = useState(plan.name);
  const [priceRupees, setPriceRupees] = useState(String(plan.price_minor / 100));
  const [description, setDescription] = useState(plan.description ?? "");
  const [maxManagers, setMaxManagers] = useState(String(plan.max_managers));
  const [maxStaff, setMaxStaff] = useState(String(plan.max_staff));
  const [config, setConfig] = useState<PlanConfig>(cfgOf(plan));

  const vertical = config.vertical ?? null;
  const catalog = useQuery({
    queryKey: ["plan-catalog", vertical],
    queryFn: () => adminPlanCatalog(token, vertical),
  });

  const draft: PlanDraft = useMemo(() => ({
    name,
    price_minor: Math.round(Number(priceRupees || "0") * 100),
    description: description.trim() || null,
    max_managers: Number(maxManagers || "0"),
    max_staff: Number(maxStaff || "0"),
    config,
  }), [name, priceRupees, description, maxManagers, maxStaff, config]);

  const [preview, setPreview] = useState<Awaited<ReturnType<typeof adminPreviewPlan>> | null>(null);
  useEffect(() => {
    let cancelled = false;
    const t = setTimeout(() => {
      adminPreviewPlan(token, draft)
        .then((p) => { if (!cancelled) setPreview(p); })
        .catch(() => { if (!cancelled) setPreview(null); });
    }, 250);
    return () => { cancelled = true; clearTimeout(t); };
  }, [token, draft]);

  const save = useMutation({
    mutationFn: () => adminUpdatePlanStructured(token, plan.id, draft),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["billing-plans"] }); onDone(); },
  });

  const blocked = (preview?.problems.length ?? 0) > 0;
  const byCategory = useMemo(() => {
    const out = new Map<string, CatalogCapability[]>();
    for (const c of catalog.data?.capabilities ?? []) {
      out.set(c.category, [...(out.get(c.category) ?? []), c]);
    }
    return [...out.entries()].sort();
  }, [catalog.data]);

  const setCfg = (patch: Partial<PlanConfig>) => setConfig((c) => ({ ...c, ...patch }));

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ink">Plan Builder</h3>
        <button onClick={onDone} className={buttonClasses("ghost", "sm")}>Close</button>
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-muted">Name
          <input value={name} onChange={(e) => setName(e.target.value)}
                 className="mt-1 w-full rounded-lg border border-line bg-raised px-2 py-1 text-sm text-ink" />
        </label>
        <label className="text-xs text-muted">Price (₹ / month)
          <input value={priceRupees} onChange={(e) => setPriceRupees(e.target.value)}
                 className="mt-1 w-full rounded-lg border border-line bg-raised px-2 py-1 text-sm text-ink" />
        </label>
        <label className="text-xs text-muted sm:col-span-2">Description
          <input value={description} onChange={(e) => setDescription(e.target.value)}
                 className="mt-1 w-full rounded-lg border border-line bg-raised px-2 py-1 text-sm text-ink" />
        </label>
        <label className="text-xs text-muted">Manager seats
          <input value={maxManagers} onChange={(e) => setMaxManagers(e.target.value)}
                 className="mt-1 w-full rounded-lg border border-line bg-raised px-2 py-1 text-sm text-ink" />
        </label>
        <label className="text-xs text-muted">Staff seats
          <input value={maxStaff} onChange={(e) => setMaxStaff(e.target.value)}
                 className="mt-1 w-full rounded-lg border border-line bg-raised px-2 py-1 text-sm text-ink" />
        </label>
        <label className="text-xs text-muted sm:col-span-2">Vertical
          <select value={vertical ?? ""}
                  onChange={(e) => setCfg({ vertical: e.target.value || null, entitlements: [] })}
                  className="mt-1 w-full rounded-lg border border-line bg-raised px-2 py-1 text-sm text-ink">
            <option value="">Generic (no vertical)</option>
            {(catalog.data?.verticals ?? []).map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
          <span className="mt-1 block text-[10px] text-muted">
            Changing the vertical clears capability selections — a plan may only include its own
            vertical&apos;s capabilities.
          </span>
        </label>
      </div>

      <div className="mt-4">
        <div className="text-xs font-semibold text-muted">Capabilities</div>
        {byCategory.map(([category, caps]) => (
          <div key={category} className="mt-2">
            <div className="text-[10px] uppercase tracking-wide text-muted">{category}</div>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {caps.map((c) => {
                const on = (config.entitlements ?? []).includes(c.key);
                return (
                  <button key={c.key} title={c.description}
                          onClick={() => setCfg({
                            entitlements: toggle(config.entitlements ?? [], c.key) })}
                          className={`rounded-lg border px-2 py-1 text-xs ${
                            on ? "border-ink bg-line-2 text-ink" : "border-line text-muted"}`}>
                    {c.label}{c.status !== "available" && <> <Chip text={c.status} tone="warn" /></>}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div>
          <div className="text-xs font-semibold text-muted">Agents</div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {(catalog.data?.agents ?? []).map((slug) => {
              const on = (config.agents ?? []).includes(slug);
              return (
                <button key={slug} onClick={() => setCfg({ agents: toggle(config.agents ?? [], slug) })}
                        className={`rounded-lg border px-2 py-1 text-xs ${
                          on ? "border-ink bg-line-2 text-ink" : "border-line text-muted"}`}>
                  {slug}
                </button>
              );
            })}
          </div>
        </div>
        <div>
          <div className="text-xs font-semibold text-muted">Channels</div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {(catalog.data?.channels ?? []).map((slug) => {
              const on = (config.channels ?? []).includes(slug);
              return (
                <button key={slug}
                        onClick={() => setCfg({ channels: toggle(config.channels ?? [], slug) })}
                        className={`rounded-lg border px-2 py-1 text-xs ${
                          on ? "border-ink bg-line-2 text-ink" : "border-line text-muted"}`}>
                  {slug}
                </button>
              );
            })}
          </div>
          <p className="mt-1 text-[10px] text-muted">
            Selecting a channel is a commercial choice — it does not connect or provision anything.
          </p>
        </div>
      </div>

      {(preview?.problems.length ?? 0) > 0 && (
        <div className="mt-4 rounded-xl border border-line bg-raised p-3">
          <div className="text-xs font-semibold text-ink">Cannot save yet</div>
          <ul className="mt-1 space-y-1">
            {preview?.problems.map((p, i) => (
              <li key={i} className="text-xs text-muted">
                <span className="font-semibold text-ink-2">{p.key}</span> — {p.reason}
                {p.fix_hint && <em className="text-muted"> · {p.fix_hint}</em>}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[10px] text-muted">
            Missing components are never added automatically — select them explicitly.
          </p>
        </div>
      )}

      {preview && (
        <div className="mt-4 rounded-xl border border-line p-3">
          <div className="text-xs font-semibold text-ink">Effective preview</div>
          <p className="mt-1 text-xs text-muted">
            Capabilities: {preview.capabilities.join(", ") || "none"}
          </p>
          <p className="text-xs text-muted">Agents: {preview.agents.join(", ") || "none"}</p>
          <p className="text-xs text-muted">Channels: {preview.channels.join(", ") || "none"}</p>
          <p className="text-xs text-muted">
            Team seats: {preview.limits.max_managers + preview.limits.max_staff}
            {" "}({preview.limits.max_managers} manager / {preview.limits.max_staff} staff;
            the owner is not counted and read-only viewers are uncapped)
          </p>
          {preview.excluded.length > 0 && (
            <p className="mt-1 text-xs text-muted">
              Excluded: {preview.excluded.map((e) => `${e.key} (${e.reason})`).join(", ")}
            </p>
          )}
          <ul className="mt-2 space-y-0.5">
            {preview.assumptions.map((a, i) => (
              <li key={i} className="text-[10px] text-muted">· assumes {a}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-4 flex items-center gap-2">
        <button disabled={blocked || save.isPending} onClick={() => save.mutate()}
                className={buttonClasses("primary", "sm")}>
          {save.isPending ? "Saving…" : "Save plan"}
        </button>
        {save.isError && (
          <span className="text-xs text-ink-2">{(save.error as Error).message}</span>
        )}
      </div>
    </Card>
  );
}
