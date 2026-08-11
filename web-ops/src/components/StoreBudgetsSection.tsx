import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { adminDeleteBudget, adminListBudgets, adminSetBudget, ApiError } from "../api";
import { rupees } from "../lib/analytics";
import { rupeesToMinor } from "../lib/plans";
import { channelLabel } from "../lib/spend";
import { buttonClasses, fieldClasses, tagClasses } from "../lib/ui";
import { Card } from "./ui";

const CHANNELS = [
  "whatsapp", "instagram", "google_ads", "social", "seo", "campaign", "subscription", "other",
];

interface Props {
  token: string;
  orgId: string;
  canRead: boolean;
  canManage: boolean;
}

export default function StoreBudgetsSection({ token, orgId, canRead, canManage }: Props) {
  const qc = useQueryClient();
  const [channel, setChannel] = useState("whatsapp");
  const [amount, setAmount] = useState("");
  const [enforce, setEnforce] = useState(false);

  const budgets = useQuery({
    queryKey: ["store-budgets", orgId],
    queryFn: () => adminListBudgets(token, orgId),
    enabled: Boolean(token) && Boolean(orgId) && canRead,
    retry: false,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["store-budgets", orgId] });
  const save = useMutation({
    mutationFn: () => adminSetBudget(token, orgId, channel, rupeesToMinor(amount), enforce),
    onSuccess: () => { setAmount(""); invalidate(); },
  });
  const remove = useMutation({
    mutationFn: (ch: string) => adminDeleteBudget(token, orgId, ch),
    onSuccess: invalidate,
  });

  if (!canRead) return null;
  const rows = budgets.data ?? [];

  return (
    <Card className="p-5">
      <div>
        <h3 className="text-sm font-semibold text-ink">Budgets &amp; caps · by channel</h3>
        <p className="text-[11px] text-muted">
          A monthly cap per channel. “Enforced” blocks a charge that would go over; otherwise it's an
          alert only.
        </p>
      </div>

      {rows.length > 0 && (
        <div className="mt-3 space-y-2">
          {rows.map((b) => {
            const pct = Math.min(100, Math.round((b.pct ?? 0)));
            const barTone = b.over ? "bg-danger" : pct >= 80 ? "bg-warn" : "bg-accent";
            return (
              <div key={b.charge_type} className="flex items-center gap-3 text-xs">
                <span className="w-24 shrink-0 text-ink-2">{channelLabel(b.charge_type)}</span>
                <span className="h-2 flex-1 overflow-hidden rounded-full bg-line-2">
                  <span className={`block h-full rounded-full ${barTone}`}
                    style={{ width: `${pct}%` }} />
                </span>
                <span className="w-40 shrink-0 text-right tnum text-ink-2">
                  {rupees(b.spent_minor)} / {rupees(b.budget_minor)}
                </span>
                <span className={tagClasses(b.over ? "danger" : b.enforce ? "accent" : "muted")}>
                  {b.over ? "over" : b.enforce ? "enforced" : "alert"}
                </span>
                {canManage && (
                  <button
                    onClick={() => remove.mutate(b.charge_type)} disabled={remove.isPending}
                    aria-label={`Remove ${b.charge_type} budget`}
                    className="shrink-0 rounded-lg px-2 py-1 text-muted hover:text-danger"
                  >
                    ×
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}

      {rows.length === 0 && !budgets.isLoading && (
        <p className="mt-3 text-sm text-muted">No budgets set for this store yet.</p>
      )}

      {canManage && (
        <form
          className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-3"
          onSubmit={(e) => { e.preventDefault(); if (rupeesToMinor(amount) > 0) save.mutate(); }}
        >
          <select
            value={channel} onChange={(e) => setChannel(e.target.value)}
            className={fieldClasses("py-1.5 text-xs")}
          >
            {CHANNELS.map((c) => <option key={c} value={c}>{channelLabel(c)}</option>)}
          </select>
          <div className="relative w-32">
            <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted">₹</span>
            <input
              value={amount} placeholder="0" inputMode="decimal"
              onChange={(e) => setAmount(e.target.value)}
              className={fieldClasses("w-full py-1.5 pl-7 text-right text-xs tnum")}
            />
          </div>
          <label className="flex items-center gap-1.5 text-xs text-ink-2">
            <input type="checkbox" checked={enforce} onChange={(e) => setEnforce(e.target.checked)} />
            Enforce (pause at cap)
          </label>
          <button
            type="submit" disabled={rupeesToMinor(amount) <= 0 || save.isPending}
            className={buttonClasses("primary", "sm")}
          >
            {save.isPending ? "Saving…" : "Set budget"}
          </button>
        </form>
      )}
      {save.isError && (
        <p className="mt-2 text-xs text-danger">
          Couldn't save — {(save.error as ApiError).message}
        </p>
      )}
    </Card>
  );
}
