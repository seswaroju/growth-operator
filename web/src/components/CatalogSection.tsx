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
import { buttonClasses, fieldClasses } from "../lib/ui";
import { Box, Plus } from "./icons";
import { Card, EmptyState, PageHeader } from "./ui";

const AVAILABILITY_OPTIONS = Object.keys(AVAILABILITY_LABEL);

// Shape-only pill — tone comes from the passed AVAILABILITY_STYLE class.
function Badge({ label, className }: { label: string; className: string }) {
  return (
    <span className={`inline-flex items-center rounded-lg px-2.5 py-1 text-[10px] font-semibold ${className}`}>
      {label}
    </span>
  );
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
  const label = "flex-1 text-xs font-medium text-muted";

  return (
    <form onSubmit={(e) => { e.preventDefault(); onSubmit(f); }} className="space-y-3">
      <input
        value={f.title}
        onChange={(e) => set({ title: e.target.value })}
        placeholder="Item name"
        className={fieldClasses("w-full")}
      />
      <div className="flex gap-3">
        <label className={label}>
          Pricing
          <select
            value={f.priceMode}
            onChange={(e) => set({ priceMode: e.target.value as "static" | "computed" })}
            disabled={priceModeLocked}
            className={fieldClasses("mt-1.5 w-full disabled:opacity-60")}
          >
            <option value="static">Fixed price</option>
            <option value="computed">Live rate</option>
          </select>
        </label>
        {f.priceMode === "static" && (
          <label className={label}>
            Price (₹)
            <input
              value={f.priceRupees}
              onChange={(e) => set({ priceRupees: e.target.value })}
              inputMode="decimal"
              placeholder="1800"
              className={fieldClasses("mt-1.5 w-full")}
            />
          </label>
        )}
      </div>
      <div className="flex gap-3">
        <label className={label}>
          SKU
          <input
            value={f.sku}
            onChange={(e) => set({ sku: e.target.value })}
            placeholder="optional"
            className={fieldClasses("mt-1.5 w-full")}
          />
        </label>
        <label className={label}>
          Availability
          <select
            value={f.availability}
            onChange={(e) => set({ availability: e.target.value })}
            className={fieldClasses("mt-1.5 w-full")}
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
        className={fieldClasses("w-full resize-y")}
      />
      {error && <p className="rounded-xl bg-danger-soft px-3 py-2 text-xs text-danger">{error}</p>}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={pending || f.title.trim().length === 0}
          className={buttonClasses("primary", "sm")}
        >
          {pending ? "Saving…" : submitLabel}
        </button>
        <button type="button" onClick={onCancel} disabled={pending} className={buttonClasses("ghost", "sm")}>
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
      <li>
        <Card className="p-4">
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
        </Card>
      </li>
    );
  }

  return (
    <li>
      <Card className="p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold text-ink">{item.title}</h3>
            {item.sku && <p className="text-[11px] text-muted">SKU {item.sku}</p>}
          </div>
          <span className="shrink-0 font-serif text-base font-medium tnum text-ink">{priceLabel(item)}</span>
        </div>
        {item.description && <p className="mt-2 line-clamp-2 text-xs text-muted">{item.description}</p>}
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
          <Badge label={availabilityLabel(item.availability)} className={AVAILABILITY_STYLE[item.availability] ?? "bg-line-2 text-ink-2"} />
          {attrs.map(([k, v]) => (
            <span key={k} className="rounded-lg bg-line-2 px-2.5 py-1 text-[10px] text-ink-2">
              {k}: {String(v)}
            </span>
          ))}
        </div>
        {canWrite && (
          <div className="mt-3.5 flex gap-2">
            <button onClick={() => setEditing(true)} className={buttonClasses("ghost", "sm")}>
              Edit
            </button>
            <button
              onClick={() => {
                if (confirm(`Archive "${item.title}"?`)) archive.mutate();
              }}
              disabled={archive.isPending}
              className={buttonClasses("danger-ghost", "sm")}
            >
              {archive.isPending ? "Archiving…" : "Archive"}
            </button>
          </div>
        )}
      </Card>
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
    <div>
      <PageHeader
        title="Catalog"
        actions={
          <>
            <form
              onSubmit={(e) => { e.preventDefault(); setQueryText(draft.trim()); }}
              className="flex items-center gap-1.5"
            >
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Search items…"
                className={fieldClasses("w-44 py-2")}
              />
              {queryText && (
                <button
                  type="button"
                  onClick={() => { setDraft(""); setQueryText(""); }}
                  className={buttonClasses("ghost", "sm")}
                >
                  Clear
                </button>
              )}
            </form>
            {canWrite && !creating && (
              <button onClick={() => setCreating(true)} className={buttonClasses("primary", "md")}>
                <Plus className="h-[15px] w-[15px]" />
                New item
              </button>
            )}
          </>
        }
      />

      <div className="space-y-4">
        {creating && (
          <Card className="p-4">
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
          </Card>
        )}

        {isLoading && <p className="text-sm text-muted">Loading…</p>}
        {isError && (
          <p className="rounded-2xl border border-danger-soft bg-danger-soft px-4 py-3 text-sm text-danger">
            Couldn't load the catalog — {(error as Error).message}
          </p>
        )}
        {data && data.length === 0 && (
          <Card>
            <EmptyState
              icon={<Box className="h-6 w-6" />}
              title={queryText ? "No items match your search" : "No catalog items yet"}
              hint={queryText ? "Try a different term." : "Add items here or import them in bulk."}
            />
          </Card>
        )}
        {data && data.length > 0 && (
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {data.map((item) => (
              <ItemCard key={item.id} item={item} canWrite={canWrite} token={token} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
