import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { adminCostMargin, ApiError } from "../api";
import { rupees } from "../lib/analytics";
import { Card } from "./ui";

interface Props {
  token: string;
  orgId: string;
  canRead: boolean;
}

function money(minor: number): string {
  return rupees(minor);
}

function marginClass(minor: number): string {
  return minor >= 0 ? "text-good" : "text-danger";
}

export default function StoreCostMarginSection({ token, orgId, canRead }: Props) {
  const [month, setMonth] = useState(""); // "" → current month (server default)
  const on = Boolean(token) && Boolean(orgId);
  const q = useQuery({
    queryKey: ["store-cost-margin", orgId, month],
    queryFn: () => adminCostMargin(token, orgId, month || undefined),
    enabled: on && canRead, retry: false,
  });

  if (!canRead) return null;
  const data = q.data;

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-ink">Cost &amp; margin</h3>
          <p className="text-[11px] text-muted">
            What you bill vs. what you spend on this store — LLM (in the plan) + each API, per month.
          </p>
        </div>
        <input
          type="month" value={month} onChange={(e) => setMonth(e.target.value)}
          aria-label="Month"
          className="rounded-xl border border-line bg-raised px-3 py-1.5 text-xs text-ink
            caret-accent outline-none focus:border-accent focus:ring-4 focus:ring-accent-soft"
        />
      </div>

      {q.isError ? (
        <p className="mt-3 text-sm text-danger">Couldn't load — {(q.error as ApiError).message}</p>
      ) : !data ? (
        <p className="mt-3 text-sm text-muted">Loading…</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="text-left text-[11px] font-medium uppercase tracking-wide text-muted">
                <th className="pb-2 pr-3">{data.month} · line</th>
                <th className="px-3 pb-2 text-right">Revenue</th>
                <th className="px-3 pb-2 text-right">Cost</th>
                <th className="px-3 pb-2 text-right">Margin</th>
              </tr>
            </thead>
            <tbody>
              {data.lines.map((ln) => (
                <tr key={ln.category} className="border-t border-line-2">
                  <td className="py-2 pr-3 text-ink-2">
                    {ln.label}
                    {ln.category === "llm" && (
                      <span className="block text-[11px] text-muted">
                        {data.llm.runs} runs · {data.llm.tokens_in + data.llm.tokens_out} tokens ·
                        {" "}${data.llm.cost_usd} @ ₹{data.usd_inr_rate}/$
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right tnum text-ink-2">{money(ln.revenue_minor)}</td>
                  <td className="px-3 py-2 text-right tnum text-ink-2">{money(ln.cost_minor)}</td>
                  <td className={`px-3 py-2 text-right tnum ${marginClass(ln.margin_minor)}`}>
                    {money(ln.margin_minor)}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-line font-semibold">
                <td className="py-2 pr-3 text-ink">Total</td>
                <td className="px-3 py-2 text-right tnum text-ink">{money(data.revenue_minor)}</td>
                <td className="px-3 py-2 text-right tnum text-ink">{money(data.cost_minor)}</td>
                <td className={`px-3 py-2 text-right tnum ${marginClass(data.margin_minor)}`}>
                  {money(data.margin_minor)}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </Card>
  );
}
