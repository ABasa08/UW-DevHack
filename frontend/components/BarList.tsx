// Single-series horizontal bars: one hue, value labels in text ink, per-bar hover title.
export default function BarList({ rows, empty, unit = "" }: { rows: { label: string; value: number; note?: string }[]; empty: string; unit?: string }) {
  if (!rows.length) return <p className="py-6 text-center text-sm text-ink-mute">{empty}</p>;
  const max = Math.max(...rows.map((r) => r.value), 1);
  return (
    <ul className="space-y-2.5" role="list">
      {rows.map((r) => (
        <li key={r.label} className="group" title={`${r.label}: ${r.value}${unit}${r.note ? ` (${r.note})` : ""}`}>
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="text-ink-soft">{r.label}</span>
            <span className="tabular-nums font-medium text-ink">
              {r.value}
              {unit}
              {r.note && <span className="ml-1 text-xs font-normal text-ink-mute">{r.note}</span>}
            </span>
          </div>
          <div className="mt-1 h-2 rounded bg-paper-sunk">
            <div className="h-2 rounded bg-brand transition-all group-hover:bg-brand-ink" style={{ width: `${(r.value / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}
