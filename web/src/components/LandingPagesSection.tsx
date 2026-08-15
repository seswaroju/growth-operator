import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchLandingPreview,
  getLandingInsights,
  getLandingPage,
  landingLifecycle,
  listLandingPages,
  listLandingVariants,
  selectLandingVariant,
  type LandingVariant,
} from "../api";
import { useAuth } from "../auth";
import { Grid } from "./icons";
import { availableActions, statusLabel, statusTone, variantBlurb } from "../lib/landing";
import { Button, Card, EmptyState, PageHeader, Tag } from "./ui";

/** A rendered candidate page. The preview needs the Bearer token, so the HTML is fetched and
 *  shown via `srcdoc` in a sandboxed frame (an `<iframe src>` could not authenticate). */
function VariantPreview({ pageId, versionNo }: { pageId: string; versionNo: number }) {
  const { token } = useAuth();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["landing", "preview", pageId, versionNo],
    queryFn: () => fetchLandingPreview(token!, pageId, versionNo),
    enabled: Boolean(token),
  });
  if (isLoading) return <div className="h-64 animate-pulse rounded-xl bg-line-2" />;
  if (isError || !data) {
    return <div className="grid h-64 place-items-center rounded-xl bg-line-2 text-sm text-muted">
      Preview unavailable
    </div>;
  }
  return (
    <iframe
      title={`Variant ${versionNo}`}
      srcDoc={data}
      sandbox=""
      className="h-64 w-full rounded-xl border border-line bg-white"
    />
  );
}

function VariantCard(
  { pageId, variant, isCurrent, canPick, onPick, picking }: {
    pageId: string;
    variant: LandingVariant;
    isCurrent: boolean;
    canPick: boolean;
    onPick: () => void;
    picking: boolean;
  },
) {
  return (
    <Card className={`p-4 ${isCurrent ? "ring-2 ring-accent" : ""}`}>
      <div className="flex items-baseline justify-between gap-2">
        <h4 className="text-sm font-semibold capitalize text-ink">{variant.variant_label}</h4>
        {isCurrent && <Tag tone="accent">Chosen</Tag>}
      </div>
      <p className="mt-1 text-[11px] text-muted">{variantBlurb(variant.variant_label)}</p>
      <div className="mt-3">
        <VariantPreview pageId={pageId} versionNo={variant.version_no} />
      </div>
      {canPick && !isCurrent && (
        <Button className="mt-3 w-full" onClick={onPick} disabled={picking}>
          {picking ? "Choosing…" : "Use this one"}
        </Button>
      )}
    </Card>
  );
}

function Insights({ pageId }: { pageId: string }) {
  const { token } = useAuth();
  const { data } = useQuery({
    queryKey: ["landing", "insights", pageId],
    queryFn: () => getLandingInsights(token!, pageId),
    enabled: Boolean(token),
  });
  if (!data || data.total_events === 0) {
    return <p className="text-sm text-muted">No visitor activity yet.</p>;
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">
        {data.total_events} interaction{data.total_events === 1 ? "" : "s"} so far
      </p>
      {data.top_items.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">
            Most wanted
          </h4>
          <ul className="mt-2 space-y-1">
            {data.top_items.slice(0, 5).map((item) => (
              <li key={item.item_ref} className="flex items-center justify-between text-sm">
                <span className="text-ink">{item.item_ref.replace(/-/g, " ")}</span>
                <span className="tabular-nums text-muted">
                  {item.clicks} click{item.clicks === 1 ? "" : "s"} · {item.views} view
                  {item.views === 1 ? "" : "s"}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function PageDetail({ pageId, onBack }: { pageId: string; onBack: () => void }) {
  const { token } = useAuth();
  const qc = useQueryClient();
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["landing"] });
  };

  const { data: page } = useQuery({
    queryKey: ["landing", "page", pageId],
    queryFn: () => getLandingPage(token!, pageId),
    enabled: Boolean(token),
  });
  const { data: variants } = useQuery({
    queryKey: ["landing", "variants", pageId],
    queryFn: () => listLandingVariants(token!, pageId),
    enabled: Boolean(token),
  });

  const pick = useMutation({
    mutationFn: (versionNo: number) => selectLandingVariant(token!, pageId, versionNo),
    onSuccess: invalidate,
  });
  const act = useMutation({
    mutationFn: (action: "publish" | "pause" | "archive") =>
      landingLifecycle(token!, pageId, action),
    onSuccess: invalidate,
  });

  if (!page) return <p className="text-sm text-muted">Loading…</p>;

  return (
    <div className="space-y-5">
      <button className="text-sm text-muted hover:text-ink" onClick={onBack}>
        ← All pages
      </button>

      <Card className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-ink">{page.slug}</h3>
            <p className="text-[11px] text-muted">
              {page.current_variant_label
                ? `Using the “${page.current_variant_label}” layout`
                : "No layout chosen yet"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Tag tone={statusTone(page.status)}>{statusLabel(page.status)}</Tag>
            {availableActions(page.status).map((action) => (
              <Button
                key={action}
                variant={action === "publish" ? "primary" : "subtle"}
                onClick={() => act.mutate(action)}
                disabled={act.isPending}
              >
                {action === "publish" ? "Publish" : action === "pause" ? "Pause" : "Archive"}
              </Button>
            ))}
          </div>
        </div>
        {act.isError && (
          <p className="mt-2 text-sm text-danger">
            {(act.error as Error).message}
          </p>
        )}
      </Card>

      <div>
        <h3 className="text-sm font-semibold text-ink">Choose a layout</h3>
        <p className="text-[11px] text-muted">
          Every version uses your own products and offer — only the layout differs. Nothing goes
          live until you publish.
        </p>
        <div className="mt-3 grid gap-4 md:grid-cols-3">
          {(variants ?? []).map((v) => (
            <VariantCard
              key={v.version_no}
              pageId={pageId}
              variant={v}
              isCurrent={page.current_version_no === v.version_no}
              canPick={page.status !== "archived"}
              onPick={() => pick.mutate(v.version_no)}
              picking={pick.isPending}
            />
          ))}
        </div>
      </div>

      <Card className="p-5">
        <h3 className="text-sm font-semibold text-ink">What visitors did</h3>
        <div className="mt-3">
          <Insights pageId={pageId} />
        </div>
      </Card>
    </div>
  );
}

export default function LandingPagesSection() {
  const { token } = useAuth();
  const [openId, setOpenId] = useState<string | null>(null);
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["landing", "pages"],
    queryFn: () => listLandingPages(token!),
    enabled: Boolean(token),
  });

  if (openId) return <PageDetail pageId={openId} onBack={() => setOpenId(null)} />;

  return (
    <div className="space-y-5">
      <PageHeader
        title="Landing pages"
        subtitle="Pages for your ads — pick a layout, publish when you're happy."
      />
      {isLoading && <p className="text-sm text-muted">Loading…</p>}
      {isError && (
        <p className="text-sm text-danger">Couldn't load pages — {(error as Error).message}</p>
      )}
      {data && data.length === 0 && (
        <EmptyState
          icon={<Grid className="h-6 w-6" />}
          title="No landing pages yet"
          hint="When you run an ad, Vaylorn builds the page it points to — you choose the layout."
        />
      )}
      {data && data.length > 0 && (
        <div className="space-y-2">
          {data.map((p) => (
            <Card key={p.id} className="p-4">
              <button
                className="flex w-full items-center justify-between gap-3 text-left"
                onClick={() => setOpenId(p.id)}
              >
                <div>
                  <div className="text-sm font-medium text-ink">{p.slug}</div>
                  <div className="text-[11px] text-muted">
                    {new Date(p.created_at).toLocaleDateString()}
                  </div>
                </div>
                <Tag tone={statusTone(p.status)}>{statusLabel(p.status)}</Tag>
              </button>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
