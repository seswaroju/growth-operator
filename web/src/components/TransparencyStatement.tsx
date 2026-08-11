import { useQuery } from "@tanstack/react-query";

import { getTransparency } from "../api";
import { useAuth } from "../auth";
import { money } from "../lib/customers";
import { channelLabel, monthLabel, roasLabel, spendShare } from "../lib/transparency";
import { Card } from "./ui";

// The owner's monthly transparency statement (OC6): what you paid, by channel, next to what your
// store earned. Grounded in your own data; never shows Growth Operator's internal cost.
export default function TransparencyStatement() {
  const { token } = useAuth();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["insights", "transparency"],
    queryFn: () => getTransparency(token as string),
    enabled: !!token,
  });

  if (isLoading) {
    return <div className="mb-6 h-40 animate-pulse rounded-2xl border border-line bg-surface" />;
  }
  if (isError || !data) return null; // non-blocking — the reports below still render

  const { spend_by_channel: channels, total_spend_minor: total } = data;

  return (
    <Card className="mb-6 p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-ink">Where your money went</h3>
          <p className="text-xs text-muted">{monthLabel(data.period_month)} · your own numbers</p>
        </div>
        <div className="flex gap-6 text-right">
          <div>
            <div className="text-[11px] font-medium text-muted">You invested</div>
            <div className="font-serif text-lg font-medium tnum text-ink">{money(total, "INR")}</div>
          </div>
          <div>
            <div className="text-[11px] font-medium text-muted">Store revenue</div>
            <div className="font-serif text-lg font-medium tnum text-ink">
              {money(data.revenue_minor, "INR")}
            </div>
          </div>
          <div>
            <div className="text-[11px] font-medium text-muted">Return</div>
            <div className={`font-serif text-lg font-medium tnum ${
              data.roas !== null && data.roas >= 1 ? "text-good" : "text-ink"}`}>
              {roasLabel(data.roas)}
            </div>
          </div>
        </div>
      </div>

      {channels.length === 0 ? (
        <p className="mt-4 text-sm text-muted">No spend recorded for this month yet.</p>
      ) : (
        <div className="mt-4 space-y-2">
          {channels.map((c) => (
            <div key={c.channel} className="flex items-center gap-3 text-xs">
              <span className="w-24 shrink-0 text-ink-2">{channelLabel(c.channel)}</span>
              <span className="h-2 flex-1 overflow-hidden rounded-full bg-line-2">
                <span
                  className="block h-full rounded-full bg-accent"
                  style={{ width: `${Math.round(spendShare(c.amount_minor, total) * 100)}%` }}
                />
              </span>
              <span className="w-20 shrink-0 text-right tnum text-ink">
                {money(c.amount_minor, "INR")}
              </span>
            </div>
          ))}
        </div>
      )}

      <p className="mt-3 text-[11px] text-muted">
        Return is your store's revenue this month divided by what you invested with Growth Operator —
        a simple ratio over your own data, not a causal claim.
      </p>
    </Card>
  );
}
