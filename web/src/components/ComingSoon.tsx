// Placeholder for a Phase-3 section that isn't built yet. Each section (Approvals, Conversations,
// Catalog, Customers, Settings) renders this until its own ticket fills it in — so the app shell is
// complete now and every later ticket is a one-file swap. Kept tasteful, not a raw "TODO".

export default function ComingSoon({ title, description }: { title: string; description: string }) {
  return (
    <section className="rounded-2xl border border-dashed border-line bg-surface p-10 text-center">
      <h2 className="font-serif text-lg font-medium text-ink">{title}</h2>
      <p className="mx-auto mt-2 max-w-md text-sm text-muted">{description}</p>
      <span className="mt-5 inline-block rounded-lg bg-accent-soft px-3 py-1 text-[11px] font-semibold text-accent-ink">
        Arriving in Phase 3
      </span>
    </section>
  );
}
