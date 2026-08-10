import type { ReactNode } from "react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getCustomer, getCustomers, type CustomerSummary } from "../api";
import { useAuth } from "../auth";
import { consentLabel, CONSENT_STYLE, money, orderStatusLabel } from "../lib/customers";
import { STAGE_LABEL, STAGE_STYLE } from "../lib/leads";
import { Users } from "./icons";
import { Card, EmptyState, PageHeader } from "./ui";

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
}

// Shape-only pill — the tone comes from the passed CONSENT_STYLE / STAGE_STYLE class.
function Badge({ label, className }: { label: string; className: string }) {
  return (
    <span className={`inline-flex items-center rounded-lg px-2.5 py-1 text-[11px] font-semibold ${className}`}>
      {label}
    </span>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <h3 className="mb-1.5 text-[11px] font-semibold text-muted">{title}</h3>
      {children}
    </div>
  );
}

function CustomerRow({
  c, selected, onSelect,
}: { c: CustomerSummary; selected: boolean; onSelect: () => void }) {
  const name = c.full_name ?? c.phone ?? "Unknown contact";
  return (
    <button
      onClick={onSelect}
      className={`w-full rounded-2xl border p-3.5 text-left transition ${
        selected ? "border-accent bg-surface ring-4 ring-accent-soft" : "border-line bg-surface hover:border-muted"
      }`}
    >
      <div className="truncate text-sm font-semibold text-ink">{name}</div>
      {c.phone && c.full_name && <div className="text-[11px] text-muted">{c.phone}</div>}
      <div className="mt-1 text-[11px] text-muted">
        {c.lead_count} lead{c.lead_count === 1 ? "" : "s"} · {c.order_count} order
        {c.order_count === 1 ? "" : "s"}
      </div>
    </button>
  );
}

function CustomerDetailPanel({ token, id, onBack }: { token: string; id: string; onBack: () => void }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["customer", id],
    queryFn: () => getCustomer(token, id),
  });

  if (isLoading) return <div className="text-sm text-muted">Loading…</div>;
  if (isError) return <div className="text-sm text-danger">{(error as Error).message}</div>;
  if (!data) return null;

  const attrs = Object.entries(data.attributes);
  const ordersTotal = data.orders.reduce((s, o) => s + o.total_minor, 0);

  return (
    <Card className="space-y-5 p-5">
      <div className="flex items-start gap-2">
        <button
          onClick={onBack}
          className="rounded-lg border border-line px-2.5 py-1 text-xs text-ink-2 hover:border-muted md:hidden"
        >
          ← Back
        </button>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="truncate font-serif text-lg font-medium">{data.full_name ?? data.phone ?? "Customer"}</h2>
            <Badge label={consentLabel(data.consent_status)} className={CONSENT_STYLE[data.consent_status] ?? "bg-line-2 text-ink-2"} />
          </div>
          <div className="mt-0.5 space-x-2 text-xs text-muted">
            {data.phone && <span>{data.phone}</span>}
            {data.email && <span>{data.email}</span>}
            <span>· since {fmtDate(data.created_at)}</span>
          </div>
        </div>
      </div>

      {(data.language_pref || attrs.length > 0) && (
        <Section title="Preferences">
          <div className="flex flex-wrap gap-1.5">
            {data.language_pref && (
              <span className="rounded-lg bg-line-2 px-2.5 py-1 text-[11px] text-ink-2">
                language: {data.language_pref}
              </span>
            )}
            {attrs.map(([k, v]) => (
              <span key={k} className="rounded-lg bg-line-2 px-2.5 py-1 text-[11px] text-ink-2">
                {k}: {String(v)}
              </span>
            ))}
          </div>
        </Section>
      )}

      <Section title={`Orders${data.orders.length ? ` · ${money(ordersTotal, data.orders[0].currency)} total` : ""}`}>
        {data.orders.length === 0 ? (
          <p className="text-sm text-muted">No orders yet.</p>
        ) : (
          <ul className="space-y-1.5">
            {data.orders.map((o) => (
              <li key={o.id} className="flex items-center justify-between rounded-lg bg-porcelain px-3 py-2 text-sm">
                <span className="tnum font-serif">{money(o.total_minor, o.currency)}</span>
                <span className="flex items-center gap-2 text-xs text-muted">
                  {orderStatusLabel(o.status)} · {fmtDate(o.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Pipeline">
        {data.leads.length === 0 ? (
          <p className="text-sm text-muted">No leads yet.</p>
        ) : (
          <ul className="space-y-1.5">
            {data.leads.map((l) => (
              <li key={l.id} className="flex items-center justify-between text-sm">
                <Badge label={STAGE_LABEL[l.stage as keyof typeof STAGE_LABEL] ?? l.stage} className={STAGE_STYLE[l.stage as keyof typeof STAGE_STYLE] ?? "bg-line-2 text-ink-2"} />
                <span className="text-xs text-muted">via {l.source} · {fmtDate(l.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Conversations">
        {data.conversations.length === 0 ? (
          <p className="text-sm text-muted">No conversations yet.</p>
        ) : (
          <ul className="space-y-1">
            {data.conversations.map((c) => (
              <li key={c.id} className="flex items-center justify-between text-xs text-muted">
                <span className="capitalize">{c.status}</span>
                <span>updated {fmtDate(c.updated_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </Card>
  );
}

export default function CustomersSection() {
  const { token } = useAuth();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["customers"],
    queryFn: () => getCustomers(token as string),
    enabled: !!token,
  });

  if (!token) return null;

  return (
    <div>
      <PageHeader title="Customers" />

      {isLoading && <p className="text-sm text-muted">Loading…</p>}
      {isError && (
        <p className="rounded-2xl border border-danger-soft bg-danger-soft px-4 py-3 text-sm text-danger">
          Couldn't load customers — {(error as Error).message}
        </p>
      )}
      {data && data.length === 0 && (
        <Card>
          <EmptyState
            icon={<Users className="h-6 w-6" />}
            title="No customers yet"
            hint="Everyone who messages your store will appear here with their history."
          />
        </Card>
      )}
      {data && data.length > 0 && (
        <div className="grid gap-4 md:grid-cols-[320px_1fr]">
          <div className={`space-y-2 ${selectedId ? "hidden md:block" : "block"}`}>
            {data.map((c) => (
              <CustomerRow
                key={c.id}
                c={c}
                selected={c.id === selectedId}
                onSelect={() => setSelectedId(c.id)}
              />
            ))}
          </div>
          <div className={selectedId ? "block" : "hidden md:block"}>
            {selectedId ? (
              <CustomerDetailPanel token={token} id={selectedId} onBack={() => setSelectedId(null)} />
            ) : (
              <div className="flex h-full min-h-40 items-center justify-center rounded-2xl border border-dashed border-line bg-surface text-sm text-muted">
                Select a customer to see their profile &amp; history
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
