import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  adminCreateTransaction, adminListTransactions, adminRequestReceipt, ApiError,
  type Transaction,
} from "../api";
import { rupees } from "../lib/analytics";
import {
  canRequestReceipt, hasChargeableLine, previewTotals, statusView, toMinor, type DraftLine,
} from "../lib/receipts";
import { buttonClasses, fieldClasses, tagClasses } from "../lib/ui";
import { Card } from "./ui";

function fmt(ts: string): string {
  return new Date(ts).toLocaleDateString(undefined, { dateStyle: "medium" });
}

const EMPTY_LINE: DraftLine = { description: "", amountRupees: "" };

interface Props {
  token: string;
  orgId: string;
  storeName: string;
  canRead: boolean;
  canManage: boolean;
}

// ---- The "New charge" form ------------------------------------------------------------------

function ChargeForm({ token, orgId, storeName, onDone }:
  { token: string; orgId: string; storeName: string; onDone: (receiptNo: string) => void }) {
  const qc = useQueryClient();
  const [lines, setLines] = useState<DraftLine[]>([{ ...EMPTY_LINE }]);
  const [discountPercent, setDiscountPercent] = useState("");
  const [discountReason, setDiscountReason] = useState("");
  const [taxLabel, setTaxLabel] = useState("GST 18%");
  const [taxRupees, setTaxRupees] = useState("");
  const [notes, setNotes] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");

  const totals = previewTotals(lines, discountPercent, taxRupees);
  const ready = hasChargeableLine(lines);

  const setLine = (i: number, patch: Partial<DraftLine>) =>
    setLines((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));

  const create = useMutation({
    mutationFn: () =>
      adminCreateTransaction(token, orgId, {
        store_name: storeName,
        line_items: lines
          .filter((l) => l.description.trim() && toMinor(l.amountRupees) > 0)
          .map((l) => ({ description: l.description.trim(), amount_minor: toMinor(l.amountRupees) })),
        discount_percent: Number.parseFloat(discountPercent) || 0,
        discount_reason: discountReason.trim() || null,
        tax_label: taxLabel.trim() || "Tax",
        tax_minor: toMinor(taxRupees),
        notes: notes.trim() || null,
        contact_email: email.trim() || null,
        contact_phone: phone.trim() || null,
      }),
    onSuccess: (tx: Transaction) => {
      qc.invalidateQueries({ queryKey: ["store-transactions", orgId] });
      onDone(tx.receipt_no);
    },
  });

  const label = "text-[11px] font-medium text-muted";

  return (
    <form
      className="space-y-3 rounded-xl border border-line bg-raised p-4"
      onSubmit={(e) => { e.preventDefault(); if (ready) create.mutate(); }}
    >
      {/* Line items */}
      <div className="space-y-2">
        <div className={label}>What are you charging for?</div>
        {lines.map((l, i) => (
          <div key={i} className="flex items-center gap-2">
            <input
              value={l.description} placeholder="e.g. Growth plan — August"
              onChange={(e) => setLine(i, { description: e.target.value })}
              className={fieldClasses("flex-1 py-2 text-[13px]")}
            />
            <div className="relative w-32 shrink-0">
              <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted">₹</span>
              <input
                value={l.amountRupees} placeholder="0" inputMode="decimal"
                onChange={(e) => setLine(i, { amountRupees: e.target.value })}
                className={fieldClasses("w-full py-2 pl-7 text-right text-[13px] tnum")}
              />
            </div>
            {lines.length > 1 && (
              <button
                type="button" aria-label="Remove line"
                onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))}
                className="shrink-0 rounded-lg px-2 py-1 text-muted hover:text-danger"
              >
                ×
              </button>
            )}
          </div>
        ))}
        <button
          type="button"
          onClick={() => setLines((ls) => [...ls, { ...EMPTY_LINE }])}
          className="text-[12px] font-semibold text-accent-ink hover:text-accent"
        >
          + Add line
        </button>
      </div>

      {/* Discount + tax */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div>
          <div className={label}>Discount %</div>
          <input
            value={discountPercent} placeholder="0" inputMode="decimal"
            onChange={(e) => setDiscountPercent(e.target.value)}
            className={fieldClasses("mt-1 w-full py-2 text-[13px] tnum")}
          />
        </div>
        <div className="col-span-1 sm:col-span-3">
          <div className={label}>Discount reason</div>
          <input
            value={discountReason} placeholder="e.g. loyal client · festival offer"
            onChange={(e) => setDiscountReason(e.target.value)}
            className={fieldClasses("mt-1 w-full py-2 text-[13px]")}
          />
        </div>
        <div className="col-span-1 sm:col-span-2">
          <div className={label}>Tax label</div>
          <input
            value={taxLabel} placeholder="GST 18%"
            onChange={(e) => setTaxLabel(e.target.value)}
            className={fieldClasses("mt-1 w-full py-2 text-[13px]")}
          />
        </div>
        <div className="col-span-1 sm:col-span-2">
          <div className={label}>Tax amount (₹)</div>
          <input
            value={taxRupees} placeholder="0" inputMode="decimal"
            onChange={(e) => setTaxRupees(e.target.value)}
            className={fieldClasses("mt-1 w-full py-2 text-right text-[13px] tnum")}
          />
        </div>
      </div>

      {/* Notes + contact */}
      <div>
        <div className={label}>Notes (kept with the transaction)</div>
        <input
          value={notes} placeholder="e.g. paid via UPI · reference #…"
          onChange={(e) => setNotes(e.target.value)}
          className={fieldClasses("mt-1 w-full py-2 text-[13px]")}
        />
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <div>
          <div className={label}>Receipt email</div>
          <input
            value={email} placeholder="owner@store.example" inputMode="email"
            onChange={(e) => setEmail(e.target.value)}
            className={fieldClasses("mt-1 w-full py-2 text-[13px]")}
          />
        </div>
        <div>
          <div className={label}>Receipt WhatsApp</div>
          <input
            value={phone} placeholder="+9190000 00000" inputMode="tel"
            onChange={(e) => setPhone(e.target.value)}
            className={fieldClasses("mt-1 w-full py-2 text-[13px]")}
          />
        </div>
      </div>

      {/* Live total preview + submit */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3">
        <div className="text-xs text-muted">
          Subtotal <span className="tnum text-ink-2">{rupees(totals.subtotal)}</span>
          {totals.discount > 0 && (
            <> · <span className="text-good">−{rupees(totals.discount)}</span></>
          )}
          {totals.tax > 0 && <> · tax <span className="tnum text-ink-2">{rupees(totals.tax)}</span></>}
          {" "}· Total <span className="font-serif text-sm font-medium tnum text-ink">{rupees(totals.total)}</span>
        </div>
        <button
          type="submit" disabled={!ready || create.isPending}
          className={buttonClasses("primary", "sm")}
        >
          {create.isPending ? "Recording…" : "Record charge"}
        </button>
      </div>
      {create.isError && (
        <p className="text-xs text-danger">
          Couldn't record the charge — {(create.error as ApiError).message}
        </p>
      )}
    </form>
  );
}

// ---- Transactions list + receipt action -----------------------------------------------------

function TxRow({ token, orgId, tx, canManage, onNote }:
  { token: string; orgId: string; tx: Transaction; canManage: boolean; onNote: (m: string) => void }) {
  const qc = useQueryClient();
  const view = statusView(tx.status);
  const request = useMutation({
    mutationFn: () => adminRequestReceipt(token, orgId, tx.id),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["store-transactions", orgId] });
      onNote(`Receipt ${r.receipt_no} queued — awaiting the owner's approval before it sends.`);
    },
  });
  return (
    <tr className="border-t border-line-2 align-top">
      <td className="py-2.5 pr-3">
        <div className="font-medium tnum text-ink">{tx.receipt_no}</div>
        <div className="text-[11px] text-muted">{fmt(tx.created_at)}</div>
      </td>
      <td className="px-3 py-2.5">
        <div className="text-[13px] text-ink-2">
          {tx.line_items.map((li) => li.description).join(" · ") || "—"}
        </div>
        {tx.discount_minor > 0 && (
          <div className="text-[11px] text-good">
            −{rupees(tx.discount_minor)} discount{tx.discount_reason ? ` · ${tx.discount_reason}` : ""}
          </div>
        )}
        {tx.notes && <div className="text-[11px] text-muted">{tx.notes}</div>}
      </td>
      <td className="px-3 py-2.5 text-right">
        <div className="font-serif text-sm font-medium tnum text-ink">{rupees(tx.total_minor)}</div>
      </td>
      <td className="px-3 py-2.5">
        <span className={tagClasses(view.tone)}>{view.label}</span>
      </td>
      <td className="py-2.5 pl-3 text-right">
        {canManage && canRequestReceipt(tx.status) ? (
          <button
            onClick={() => request.mutate()} disabled={request.isPending}
            className={buttonClasses("ghost", "sm")}
          >
            {request.isPending ? "Sending…" : "Request receipt"}
          </button>
        ) : (
          <span className="text-[11px] text-muted">
            {tx.status === "receipted" ? "sent" : tx.status === "paid" ? "pending" : ""}
          </span>
        )}
      </td>
    </tr>
  );
}

export default function StorePaymentsSection(
  { token, orgId, storeName, canRead, canManage }: Props,
) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const txs = useQuery({
    queryKey: ["store-transactions", orgId],
    queryFn: () => adminListTransactions(token, orgId),
    enabled: Boolean(token) && Boolean(orgId) && canRead,
    retry: false,
  });

  if (!canRead) return null;

  const rows = txs.data ?? [];

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-ink">Payments · charge this store</h3>
          <p className="text-[11px] text-muted">
            Record a charge, then request a receipt — it sends to email + WhatsApp only after the
            owner approves.
          </p>
        </div>
        {canManage && (
          <button
            onClick={() => { setOpen((v) => !v); setNote(null); }}
            className={buttonClasses(open ? "ghost" : "primary", "sm")}
          >
            {open ? "Close" : "New charge"}
          </button>
        )}
      </div>

      {note && (
        <p className="mt-3 rounded-xl bg-good-soft px-3 py-2 text-xs font-medium text-good">{note}</p>
      )}

      {open && canManage && (
        <div className="mt-3">
          <ChargeForm
            token={token} orgId={orgId} storeName={storeName}
            onDone={(receiptNo) => {
              setOpen(false);
              setNote(`Charge ${receiptNo} recorded. Use “Request receipt” to send it for approval.`);
            }}
          />
        </div>
      )}

      <div className="mt-4 overflow-x-auto">
        {txs.isLoading ? (
          <p className="text-sm text-muted">Loading…</p>
        ) : txs.isError ? (
          <p className="text-sm text-danger">
            Couldn't load transactions — {(txs.error as ApiError).message}
          </p>
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted">No charges recorded for this store yet.</p>
        ) : (
          <table className="w-full border-collapse">
            <thead>
              <tr className="text-left text-[11px] font-medium uppercase tracking-wide text-muted">
                <th className="pb-2 pr-3">Receipt</th>
                <th className="px-3 pb-2">For</th>
                <th className="px-3 pb-2 text-right">Total</th>
                <th className="px-3 pb-2">Receipt status</th>
                <th className="pb-2 pl-3" />
              </tr>
            </thead>
            <tbody>
              {rows.map((tx) => (
                <TxRow
                  key={tx.id} token={token} orgId={orgId} tx={tx}
                  canManage={canManage} onNote={setNote}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Card>
  );
}
