/** Shown on a page while its data comes from the synthetic demo fallback. */
export function DemoBadge({ asOf }: { asOf?: string }) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="inline-flex items-center gap-1.5 rounded-full border border-bronze-600/40 bg-bronze-600/10 px-2.5 py-0.5 text-[11px] font-medium uppercase tracking-[0.14em] text-bronze-300">
        <span className="h-1.5 w-1.5 rounded-full bg-bronze-400" />
        Illustrative data
      </span>
      {asOf && (
        <span className="text-xs text-slate-500">
          Sample as of {asOf} · live feed unavailable
        </span>
      )}
    </div>
  );
}
