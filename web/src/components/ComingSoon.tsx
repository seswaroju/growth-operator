// Placeholder for a Phase-3 section that isn't built yet. Each section (Approvals, Conversations,
// Catalog, Customers, Settings) renders this until its own ticket fills it in — so the app shell is
// complete now and every later ticket is a one-file swap. Kept tasteful, not a raw "TODO".

export default function ComingSoon({ title, description }: { title: string; description: string }) {
  return (
    <section className="rounded-2xl border border-dashed border-neutral-300 bg-white p-10 text-center">
      <h2 className="text-base font-semibold text-neutral-800">{title}</h2>
      <p className="mx-auto mt-2 max-w-md text-sm text-neutral-500">{description}</p>
      <span className="mt-5 inline-block rounded-full bg-neutral-100 px-3 py-1 text-[11px] font-medium text-neutral-500">
        Arriving in Phase 3
      </span>
    </section>
  );
}
