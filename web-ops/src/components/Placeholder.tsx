export default function Placeholder({ title, note }: { title: string; note: string }) {
  return (
    <section className="rounded-2xl border border-dashed border-slate-700 bg-slate-800/30 p-6">
      <h2 className="text-sm font-semibold text-slate-200">{title}</h2>
      <p className="mt-1 text-sm text-slate-400">{note}</p>
    </section>
  );
}
