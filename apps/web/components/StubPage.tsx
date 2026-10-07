import { Card } from "./ui/card";

/** Placeholder for nav sections not yet built — keeps the IA complete. */
export function StubPage({
  title,
  phase,
  description,
}: {
  title: string;
  phase: string;
  description: string;
}) {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">{title}</h1>
        <p className="mt-1 text-sm text-slate-500">{phase} — coming soon</p>
      </div>
      <Card title="Planned scope">
        <p className="text-sm text-slate-400">{description}</p>
        <p className="mt-3 text-xs text-slate-600">
          This section will follow the same contract as the rest of the
          platform: envelope API responses with as-of timestamps, data_status on
          every payload, whole-percent probabilities, and the probabilistic
          disclaimer on every forecast surface.
        </p>
      </Card>
    </div>
  );
}
