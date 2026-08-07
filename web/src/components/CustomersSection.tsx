import type { ReactNode } from "react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getCustomer, getCustomers, type CustomerSummary } from "../api";
import { useAuth } from "../auth";
import { consentLabel, CONSENT_STYLE, money, orderStatusLabel } from "../lib/customers";
import { STAGE_LABEL, STAGE_STYLE } from "../lib/leads";

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
}

function Badge({ label, className }: { label: string; className: string }) {
  return <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${className}`}>{label}</span>;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-neutral-400">{title}</h3>
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
      className={`w-full rounded-xl border p-3 text-left transition ${
        selected ? "border-neutral-900 bg-white" : "border-neutral-200 bg-white hover:border-neutral-400"
      }`}
    >
      <div className="truncate text-sm font-medium text-neutral-900">{name}</div>
      {c.phone && c.full_name && <div className="text-[11px] text-neutral-400">{c.phone}</div>}
      <div className="mt-1 text-[11px] text-neutral-500">
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

  if (isLoading) return <div className="text-sm text-neutral-500">Loading…</div>;
  if (isError) return <div className="text-sm text-red-700">{(error as Error).message}</div>;
  if (!data) return null;

  const attrs = Object.entries(data.attributes);
  const ordersTotal = data.orders.reduce((s, o) => s + o.total_minor, 0);

  return (
    <div className="space-y-4 rounded-2xl border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="flex items-start gap-2">
        <button
          onClick={onBack}
          className="rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-600 hover:bg-neutral-50 md:hidden"
        >
          ← Back
        </button>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-base font-semibold">{data.full_name ?? data.phone ?? "Customer"}</h2>
            <Badge label={consentLabel(data.consent_status)} className={CONSENT_STYLE[data.consent_status] ?? "bg-neutral-100 text-neutral-600"} />
          </div>
          <div className="mt-0.5 space-x-2 text-xs text-neutral-500">
            {data.phone && <span>{data.phone}</span>}
            {data.email && <span>{data.email}</span>}
            <span>· since {fmtDate(data.created_at)}</span>
          </div>
        </div>
      </div>

      {(data.language_pref || attrs.length > 0) && (
        <Section title="Preferences">
          <div className="flex flex-wrap gap-1">
            {data.language_pref && (
              <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] text-neutral-600">
                language: {data.language_pref}
              </span>
            )}
            {attrs.map(([k, v]) => (
              <span key={k} className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] text-neutral-600">
                {k}: {String(v)}
              </span>
            ))}
          </div>
        </Section>
      )}

      <Section title={`Orders${data.orders.length ? ` · ${money(ordersTotal, data.orders[0].currency)} total` : ""}`}>
        {data.orders.length === 0 ? (
          <p className="text-sm text-neutral-400">No orders yet.</p>
        ) : (
          <ul className="space-y-1.5">
            {data.orders.map((o) => (
              <li key={o.id} className="flex items-center justify-between rounded-lg bg-neutral-50 px-3 py-2 text-sm">
                <span className="tabular-nums font-medium">{money(o.total_minor, o.currency)}</span>
                <span className="flex items-center gap-2 text-xs text-neutral-500">
                  {orderStatusLabel(o.status)} · {fmtDate(o.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Pipeline">
        {data.leads.length === 0 ? (
          <p className="text-sm text-neutral-400">No leads yet.</p>
        ) : (
          <ul className="space-y-1.5">
            {data.leads.map((l) => (
              <li key={l.id} className="flex items-center justify-between text-sm">
                <Badge label={STAGE_LABEL[l.stage as keyof typeof STAGE_LABEL] ?? l.stage} className={STAGE_STYLE[l.stage as keyof typeof STAGE_STYLE] ?? "bg-neutral-100 text-neutral-600"} />
                <span className="text-xs text-neutral-500">via {l.source} · {fmtDate(l.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Conversations">
        {data.conversations.length === 0 ? (
          <p className="text-sm text-neutral-400">No conversations yet.</p>
        ) : (
          <ul className="space-y-1">
            {data.conversations.map((c) => (
              <li key={c.id} className="flex items-center justify-between text-xs text-neutral-500">
                <span className="capitalize">{c.status}</span>
                <span>updated {fmtDate(c.updated_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
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
    <div className="space-y-4">
      <h1 className="text-lg font-semibold tracking-tight">Customers</h1>

      {isLoading && <p className="text-sm text-neutral-500">Loading…</p>}
      {isError && (
        <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          Couldn't load customers — {(error as Error).message}
        </p>
      )}
      {data && data.length === 0 && (
        <div className="rounded-2xl border border-dashed border-neutral-300 bg-white p-10 text-center">
          <p className="text-sm font-medium text-neutral-700">No customers yet</p>
          <p className="mt-1 text-sm text-neutral-500">
            Everyone who messages your store will appear here with their history.
          </p>
        </div>
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
              <div className="flex h-full min-h-40 items-center justify-center rounded-2xl border border-dashed border-neutral-300 bg-white text-sm text-neutral-400">
                Select a customer to see their profile &amp; history
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
