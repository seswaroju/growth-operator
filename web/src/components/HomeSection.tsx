import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { getInsightsSummary, getOverview, type Overview } from "../api";
import { useAuth } from "../auth";
import { delta, formatValue } from "../lib/insights";
import { buttonClasses } from "../lib/ui";
import { ArrowRight, CheckCircle, Grid, MessageCircle, Ticket } from "./icons";
import { Card, PageHeader, Stat } from "./ui";

// Quick-links to the secondary counts — clearly subordinate to the focal "needs you" panel, so the
// dashboard has hierarchy instead of four identical stat cards.
const QUICK = [
  { key: "open_conversations", label: "Open conversations", to: "/conversations", Icon: MessageCircle },
  { key: "catalog_items", label: "Catalog items", to: "/catalog", Icon: Grid },
  { key: "open_tickets", label: "Support tickets", to: "/support", Icon: Ticket },
] as const;

function DashboardSkeleton() {
  return (
    <div className="grid gap-5 lg:grid-cols-[1.7fr_1fr]">
      <div className="h-52 animate-pulse rounded-2xl border border-line bg-surface" />
      <div className="h-52 animate-pulse rounded-2xl border border-line bg-surface" />
    </div>
  );
}

// Focal: approvals are the owner's core daily act — nothing sends until they approve — so they lead.
function NeedsYou({ pending }: { pending: number }) {
  if (pending === 0) {
    return (
      <Card className="flex flex-col justify-center p-7">
        <span className="grid h-11 w-11 place-items-center rounded-xl bg-good-soft text-good">
          <CheckCircle className="h-6 w-6" />
        </span>
        <h2 className="mt-4 font-serif text-xl font-medium">You're all caught up</h2>
        <p className="mt-1 text-sm text-muted">
          No drafts are waiting on you. New replies and campaigns will land here for your OK before
          anything goes out.
        </p>
      </Card>
    );
  }
  return (
    <Card className="flex flex-col justify-between gap-6 p-7">
      <div>
        <div className="text-[12.5px] font-medium text-muted">Waiting for your OK</div>
        <div className="mt-2 flex items-baseline gap-3">
          <span className="font-serif text-5xl font-medium tnum leading-none">{pending}</span>
          <span className="text-sm text-ink-2">
            {pending === 1 ? "draft needs you" : "drafts need you"}
          </span>
        </div>
        <p className="mt-3 max-w-md text-sm text-muted">
          Replies and campaigns stay here until you approve them — nothing reaches a customer without
          your review.
        </p>
      </div>
      <Link to="/approvals" className={buttonClasses("primary", "md", "self-start")}>
        Review approvals
        <ArrowRight className="h-[15px] w-[15px]" />
      </Link>
    </Card>
  );
}

// Proof: this week's revenue + two supporting outcomes, from the analytics engine (real deltas).
function ThisWeek({ token }: { token: string }) {
  const { data } = useQuery({
    queryKey: ["insights", "summary"],
    queryFn: () => getInsightsSummary(token),
    enabled: !!token,
  });
  const byKey = new Map((data ?? []).map((m) => [m.metric_key, m]));
  const rev = byKey.get("revenue_minor");
  const leads = byKey.get("leads_created");
  const orders = byKey.get("orders");
  const anyActivity = (data ?? []).some((m) => m.this_week > 0 || m.last_week > 0);
  const d = delta(rev?.delta_pct ?? null);

  return (
    <Card className="p-7">
      {!anyActivity ? (
        <>
          <div className="text-[12.5px] font-medium text-muted">This week</div>
          <p className="mt-3 text-sm text-muted">
            Once your store is active, your results — inquiries, quotes, sales, revenue — show here with
            how they compare to last week.
          </p>
        </>
      ) : (
        <>
          <Stat
            label="Revenue · this week"
            value={formatValue("revenue_minor", rev?.this_week ?? 0)}
            delta={`${d.text} vs last week`}
            dir={d.dir}
            serif
          />
          <div className="my-5 h-px bg-line-2" />
          <div className="flex items-center justify-between py-1.5">
            <span className="text-[13px] text-ink-2">Leads created</span>
            <span className="font-serif text-lg tnum">{formatValue("leads_created", leads?.this_week ?? 0)}</span>
          </div>
          <div className="flex items-center justify-between py-1.5">
            <span className="text-[13px] text-ink-2">Orders</span>
            <span className="font-serif text-lg tnum">{formatValue("orders", orders?.this_week ?? 0)}</span>
          </div>
          <Link
            to="/insights"
            className="mt-4 inline-flex items-center gap-1 text-[12.5px] font-semibold text-accent-ink
              hover:text-accent"
          >
            See all insights
            <ArrowRight className="h-[15px] w-[15px]" />
          </Link>
        </>
      )}
    </Card>
  );
}

function QuickLinks({ data }: { data: Overview }) {
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      {QUICK.map(({ key, label, to, Icon }) => (
        <Link
          key={key}
          to={to}
          className="group flex items-center gap-3.5 rounded-2xl border border-line bg-surface p-4
            shadow-card transition hover:border-muted"
        >
          <span className="grid h-10 w-10 flex-none place-items-center rounded-xl bg-accent-soft
            text-accent-ink">
            <Icon className="h-[18px] w-[18px]" />
          </span>
          <div>
            <div className="font-serif text-xl font-medium tnum leading-none">{data[key]}</div>
            <div className="mt-1 text-[12.5px] text-muted">{label}</div>
          </div>
          <ArrowRight className="ml-auto h-4 w-4 text-muted transition group-hover:text-ink" />
        </Link>
      ))}
    </div>
  );
}

export default function HomeSection() {
  const { token, me } = useAuth();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["overview"],
    queryFn: () => getOverview(token as string),
    enabled: !!token,
  });

  const storeName = me?.org?.name ?? "your store";
  const pending = data?.pending_approvals ?? 0;
  const open = data?.open_conversations ?? 0;
  const subtitle =
    data && (pending > 0 || open > 0)
      ? `${pending} ${pending === 1 ? "approval" : "approvals"} waiting · ${open} open ${open === 1 ? "conversation" : "conversations"}`
      : `Here's what's happening at ${storeName}.`;

  return (
    <div>
      <PageHeader title="Welcome back" subtitle={subtitle} />

      {isLoading && <DashboardSkeleton />}
      {isError && (
        <p className="rounded-2xl border border-danger-soft bg-danger-soft px-4 py-3 text-sm text-danger">
          Couldn't load your overview — {(error as Error).message}
        </p>
      )}

      {data && (
        <div className="space-y-5">
          <div className="grid gap-5 lg:grid-cols-[1.7fr_1fr]">
            <NeedsYou pending={pending} />
            {token && <ThisWeek token={token} />}
          </div>
          <QuickLinks data={data} />
        </div>
      )}
    </div>
  );
}
