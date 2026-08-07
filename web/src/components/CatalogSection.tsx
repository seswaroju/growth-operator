import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  archiveCatalogItem,
  createCatalogItem,
  getCatalogItems,
  searchCatalog,
  updateCatalogItem,
  type CatalogItem,
} from "../api";
import { useAuth } from "../auth";
import { hasPermission } from "../lib/roles";
import { availabilityLabel, AVAILABILITY_LABEL, AVAILABILITY_STYLE, priceLabel, rupeesToMinor } from "../lib/catalog";

const AVAILABILITY_OPTIONS = Object.keys(AVAILABILITY_LABEL);
const inputBase =
  "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none " +
  "focus:border-neutral-900 focus:ring-1 focus:ring-neutral-900";
const btn = "rounded-lg px-3 py-1.5 text-xs font-medium transition disabled:opacity-50";

function Badge({ label, className }: { label: string; className: string }) {
  return <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${className}`}>{label}</span>;
}

// ---- Create / edit form (shared shape) -------------------------------------

interface FormState {
  title: string;
  priceMode: "static" | "computed";
  priceRupees: string;
  sku: string;
  description: string;
  availability: string;
}

function ItemForm({
  initial, priceModeLocked, submitLabel, onSubmit, onCancel, pending, error,
}: {
  initial: FormState;
  priceModeLocked: boolean;
  submitLabel: string;
  onSubmit: (f: FormState) => void;
  onCancel: () => void;
  pending: boolean;
  error: string | null;
}) {
  const [f, setF] = useState(initial);
  const set = (patch: Partial<FormState>) => setF((prev) => ({ ...prev, ...patch }));

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(f);
      }}
      className="space-y-3"
    >
      <input
        value={f.title}
        onChange={(e) => set({ title: e.target.value })}
        placeholder="Item name"
        className={inputBase}
      />
      <div className="flex gap-3">
        <label className="flex-1 text-xs text-neutral-500">
          Pricing
          <select
            value={f.priceMode}
            onChange={(e) => set({ priceMode: e.target.value as "static" | "computed" })}
            disabled={priceModeLocked}
            className="mt-1 w-full rounded-lg border border-neutral-300 px-2 py-2 text-sm disabled:bg-neutral-50"
          >
            <option value="static">Fixed price</option>
            <option value="computed">Live gold rate</option>
          </select>
        </label>
        {f.priceMode === "static" && (
          <label className="flex-1 text-xs text-neutral-500">
            Price (₹)
            <input
              value={f.priceRupees}
              onChange={(e) => set({ priceRupees: e.target.value })}
              inputMode="decimal"
              placeholder="1800"
              className={`${inputBase} mt-1`}
            />
          </label>
        )}
      </div>
      <div className="flex gap-3">
        <label className="flex-1 text-xs text-neutral-500">
          SKU
          <input
            value={f.sku}
            onChange={(e) => set({ sku: e.target.value })}
            placeholder="optional"
            className={`${inputBase} mt-1`}
          />
        </label>
        <label className="flex-1 text-xs text-neutral-500">
          Availability
          <select
            value={f.availability}
            onChange={(e) => set({ availability: e.target.value })}
            className="mt-1 w-full rounded-lg border border-neutral-300 px-2 py-2 text-sm"
          >
            {AVAILABILITY_OPTIONS.map((a) => (
              <option key={a} value={a}>{availabilityLabel(a)}</option>
            ))}
          </select>
        </label>
      </div>
      <textarea
        value={f.description}
        onChange={(e) => set({ description: e.target.value })}
        placeholder="Description (optional)"
        rows={2}
        className={`${inputBase} resize-y`}
      />
      {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error}</p>}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={pending || f.title.trim().length === 0}
          className={`${btn} bg-neutral-900 text-white hover:bg-neutral-700`}
        >
          {pending ? "Saving…" : submitLabel}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={pending}
          className={`${btn} border border-neutral-300 text-neutral-700 hover:bg-neutral-50`}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

// ---- One item card (view ↔ edit) -------------------------------------------

function ItemCard({ item, canWrite, token }: { item: CatalogItem; canWrite: boolean; token: string }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const invalidate = () => qc.invalidateQueries({ queryKey: ["catalog"] });

  const update = useMutation({
    mutationFn: (f: FormState) =>
      updateCatalogItem(token, item.id, {
        title: f.title,
        description: f.description || null,
        sku: f.sku || null,
        availability: f.availability,
        base_price_minor: f.priceMode === "static" ? rupeesToMinor(f.priceRupees) : undefined,
        reason: "edit",
      }),
    onSuccess: () => {
      setEditing(false);
      invalidate();
    },
  });
  const archive = useMutation({
    mutationFn: () => archiveCatalogItem(token, item.id),
    onSuccess: invalidate,
  });

  const attrs = Object.entries(item.attributes).slice(0, 4);

  if (editing) {
    return (
      <li className="rounded-2xl border border-neutral-200 bg-white p-4 shadow-sm">
        <ItemForm
          initial={{
            title: item.title,
            priceMode: item.price_mode === "computed" ? "computed" : "static",
            priceRupees: item.base_price_minor != null ? String(item.base_price_minor / 100) : "",
            sku: item.sku ?? "",
            description: item.description ?? "",
            availability: item.availability,
          }}
          priceModeLocked
          submitLabel="Save changes"
          onSubmit={(f) => update.mutate(f)}
          onCancel={() => setEditing(false)}
          pending={update.isPending}
          error={update.isError ? (update.error as Error).message : null}
        />
      </li>
    );
  }

  return (
    <li className="rounded-2xl border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-neutral-900">{item.title}</h3>
          {item.sku && <p className="text-[11px] text-neutral-400">SKU {item.sku}</p>}
        </div>
        <span className="shrink-0 text-sm font-semibold tabular-nums text-neutral-900">
          {priceLabel(item)}
        </span>
      </div>
      {item.description && <p className="mt-2 line-clamp-2 text-xs text-neutral-500">{item.description}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-1">
        <Badge label={availabilityLabel(item.availability)} className={AVAILABILITY_STYLE[item.availability] ?? "bg-neutral-100 text-neutral-600"} />
        {attrs.map(([k, v]) => (
          <span key={k} className="rounded-full bg-neutral-100 px-2 py-0.5 text-[10px] text-neutral-500">
            {k}: {String(v)}
          </span>
        ))}
      </div>
      {canWrite && (
        <div className="mt-3 flex gap-2">
          <button
            onClick={() => setEditing(true)}
            className={`${btn} border border-neutral-300 text-neutral-700 hover:bg-neutral-50`}
          >
            Edit
          </button>
          <button
            onClick={() => {
              if (confirm(`Archive "${item.title}"?`)) archive.mutate();
            }}
            disabled={archive.isPending}
            className={`${btn} border border-red-300 text-red-700 hover:bg-red-50`}
          >
            {archive.isPending ? "Archiving…" : "Archive"}
          </button>
        </div>
      )}
    </li>
  );
}

// ---- Section ---------------------------------------------------------------

export default function CatalogSection() {
  const { token, me } = useAuth();
  const qc = useQueryClient();
  const canWrite = hasPermission(me?.roles ?? [], "catalog:write");
  const [queryText, setQueryText] = useState(""); // committed search term
  const [draft, setDraft] = useState(""); // input value
  const [creating, setCreating] = useState(false);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["catalog", queryText],
    queryFn: async () =>
      queryText
        ? (await searchCatalog(token as string, queryText)).results
        : (await getCatalogItems(token as string)).items,
    enabled: !!token,
  });

  const create = useMutation({
    mutationFn: (f: FormState) =>
      createCatalogItem(token as string, {
        title: f.title,
        price_mode: f.priceMode,
        base_price_minor: f.priceMode === "static" ? rupeesToMinor(f.priceRupees) : null,
        sku: f.sku || null,
        description: f.description || null,
        availability: f.availability,
      }),
    onSuccess: () => {
      setCreating(false);
      qc.invalidateQueries({ queryKey: ["catalog"] });
    },
  });

  if (!token) return null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold tracking-tight">Catalog</h1>
        <div className="flex items-center gap-2">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setQueryText(draft.trim());
            }}
            className="flex items-center gap-1"
          >
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Search items…"
              className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm outline-none focus:border-neutral-900"
            />
            {queryText && (
              <button
                type="button"
                onClick={() => {
                  setDraft("");
                  setQueryText("");
                }}
                className={`${btn} border border-neutral-300 text-neutral-600 hover:bg-neutral-50`}
              >
                Clear
              </button>
            )}
          </form>
          {canWrite && !creating && (
            <button
              onClick={() => setCreating(true)}
              className={`${btn} bg-neutral-900 text-white hover:bg-neutral-700`}
            >
              New item
            </button>
          )}
        </div>
      </div>

      {creating && (
        <div className="rounded-2xl border border-neutral-200 bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold">New item</h2>
          <ItemForm
            initial={{ title: "", priceMode: "static", priceRupees: "", sku: "", description: "", availability: "in_stock" }}
            priceModeLocked={false}
            submitLabel="Create item"
            onSubmit={(f) => create.mutate(f)}
            onCancel={() => setCreating(false)}
            pending={create.isPending}
            error={create.isError ? (create.error as Error).message : null}
          />
        </div>
      )}

      {isLoading && <p className="text-sm text-neutral-500">Loading…</p>}
      {isError && (
        <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          Couldn't load the catalog — {(error as Error).message}
        </p>
      )}
      {data && data.length === 0 && (
        <div className="rounded-2xl border border-dashed border-neutral-300 bg-white p-10 text-center">
          <p className="text-sm font-medium text-neutral-700">
            {queryText ? "No items match your search" : "No catalog items yet"}
          </p>
          <p className="mt-1 text-sm text-neutral-500">
            {queryText ? "Try a different term." : "Add items here or import them in bulk."}
          </p>
        </div>
      )}
      {data && data.length > 0 && (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {data.map((item) => (
            <ItemCard key={item.id} item={item} canWrite={canWrite} token={token} />
          ))}
        </ul>
      )}
    </div>
  );
}
