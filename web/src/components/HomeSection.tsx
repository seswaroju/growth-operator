import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { getOverview, type Overview } from "../api";
import { useAuth } from "../auth";
import { HOME_TILES } from "../lib/home";

function TileShell({ children }: { children: ReactNode }) {
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-4">{children}</div>
  );
}

function SkeletonTiles() {
  return (
    <TileShell>
      {HOME_TILES.map((t) => (
        <div key={t.key} className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm">
          <div className="h-8 w-12 animate-pulse rounded bg-neutral-100" />
          <div className="mt-3 h-3 w-24 animate-pulse rounded bg-neutral-100" />
        </div>
      ))}
    </TileShell>
  );
}

function Tiles({ data }: { data: Overview }) {
  return (
    <TileShell>
      {HOME_TILES.map((t) => (
        <Link
          key={t.key}
          to={t.to}
          className="group rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm transition hover:border-neutral-400 hover:shadow"
        >
          <div className="text-3xl font-semibold tabular-nums text-neutral-900">{data[t.key]}</div>
          <div className="mt-2 text-sm font-medium text-neutral-700">{t.label}</div>
          <div className="text-xs text-neutral-400">{t.hint}</div>
        </Link>
      ))}
    </TileShell>
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

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Welcome back</h1>
        <p className="text-sm text-neutral-500">Here's what's happening at {storeName} right now.</p>
      </div>

      {isLoading && <SkeletonTiles />}
      {isError && (
        <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          Couldn't load your overview — {(error as Error).message}
        </p>
      )}
      {data && <Tiles data={data} />}

      {/* The plain-language "what worked / what didn't" outcome cards + ROI land with the
          analytics engine (Phase 3.5); the CEO-grade math lives in the operator console. */}
      <section className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-neutral-800">Results &amp; what's working</h2>
        <p className="mt-1 text-sm text-neutral-500">
          Once your campaigns are running, this is where you'll see plain-language results —
          what worked, by how much, and what to do next. Nothing to show yet.
        </p>
      </section>
    </div>
  );
}
