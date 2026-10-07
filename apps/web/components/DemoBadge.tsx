import { Badge } from "./ui/badge";

/** Shown on every page while its data comes from the synthetic demo fallback. */
export function DemoBadge({ asOf }: { asOf?: string }) {
  return (
    <div className="flex items-center gap-3">
      <Badge tone="demo">Demo data · synthetic</Badge>
      {asOf && (
        <span className="text-xs text-slate-500">
          as-of {asOf} · API unreachable — values are illustrative, not real
          market data
        </span>
      )}
    </div>
  );
}
