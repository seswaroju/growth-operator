import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { adminGetInvoice, adminListInvoices } from "../api";
import { rupees } from "../lib/analytics";
import { channelLabel } from "../lib/spend";
import { Card } from "./ui";

function monthLabel(period: string): string {
  const [y, m] = period.split("-").map(Number);
  if (!y || !m) return period;
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

function InvoiceDetail({ token, orgId, month }:
  { token: string; orgId: string; month: string }) {
  const { data } = useQuery({
    queryKey: ["store-invoice", orgId, month],
    queryFn: () => adminGetInvoice(token, orgId, month),
    retry: false,
  });
  if (!data) return <p className="px-4 py-3 text-xs text-muted">Loading…</p>;
  return (
    <div className="space-y-1 border-t border-line-2 px-4 py-3">
      {data.line_items.map((li) => (
        <div key={li.charge_type} className="flex justify-between text-xs">
          <span className="text-ink-2">{channelLabel(li.charge_type)}</span>
          <span className="tnum text-ink">{rupees(li.amount_minor)}</span>
        </div>
      ))}
      <div className="flex justify-between border-t border-line-2 pt-1 text-xs font-semibold">
        <span className="text-ink-2">Total</span>
        <span className="tnum text-ink">{rupees(data.total_minor)}</span>
      </div>
    </div>
  );
}

function InvoiceRow({ token, orgId, invoiceNo, period, total }:
  { token: string; orgId: string; invoiceNo: string; period: string; total: number }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-surface">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-raised"
      >
        <div>
          <div className="text-sm font-medium tnum text-ink">{invoiceNo}</div>
          <div className="text-[11px] text-muted">{monthLabel(period)}</div>
        </div>
        <div className="flex items-center gap-3">
          <span className="font-serif text-sm font-medium tnum text-ink">{rupees(total)}</span>
          <span className="text-lg leading-none text-muted">{open ? "–" : "+"}</span>
        </div>
      </button>
      {open && <InvoiceDetail token={token} orgId={orgId} month={period} />}
    </div>
  );
}

interface Props {
  token: string;
  orgId: string;
  canRead: boolean;
}

// Monthly invoices/statements from recorded charges (OC12), on the store-360.
export default function StoreInvoicesSection({ token, orgId, canRead }: Props) {
  const invoices = useQuery({
    queryKey: ["store-invoices", orgId],
    queryFn: () => adminListInvoices(token, orgId),
    enabled: Boolean(token) && Boolean(orgId) && canRead,
    retry: false,
  });

  if (!canRead) return null;
  const rows = invoices.data ?? [];

  return (
    <Card className="p-5">
      <h3 className="text-sm font-semibold text-ink">Invoices · monthly statements</h3>
      <p className="text-[11px] text-muted">
        One statement per month, generated from this store's recorded charges (what they pay).
      </p>
      {invoices.isLoading ? (
        <p className="mt-3 text-sm text-muted">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="mt-3 text-sm text-muted">No invoices yet — no charges recorded.</p>
      ) : (
        <div className="mt-3 space-y-2">
          {rows.map((r) => (
            <InvoiceRow
              key={r.period_month} token={token} orgId={orgId} invoiceNo={r.invoice_no}
              period={r.period_month} total={r.total_minor}
            />
          ))}
        </div>
      )}
    </Card>
  );
}
