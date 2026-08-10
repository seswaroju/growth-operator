export default function Placeholder({ title, note }: { title: string; note: string }) {
  return (
    <section className="rounded-2xl border border-dashed border-line bg-surface p-6">
      <h2 className="text-sm font-semibold text-ink">{title}</h2>
      <p className="mt-1 text-sm text-muted">{note}</p>
    </section>
  );
}
