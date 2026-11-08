export const dynamic = "force-static";
// Revalidate hourly so the served page refreshes from the API instead of
// showing the build-time snapshot forever (see apps/web/app/page.tsx).
export const revalidate = 3600;
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, Td } from "@/components/ui/table";
import {
  DataErrorBanner,
  DemoBadge,
  SyntheticTag,
  provenanceOf,
} from "@/components/DemoBadge";
import { CalibrationChart } from "@/components/charts/CalibrationChart";
import {
  DISCLAIMER,
  count0,
  fmtDateTime,
  metric3,
  pct,
} from "@/lib/format";

const TARGETS = [
  { metric: "Brier (20d)", target: "< 0.22" },
  { metric: "Calibration error (20d)", target: "< 0.04" },
  { metric: "ROC-AUC (20d)", target: "> 0.56" },
  { metric: "Min samples / bucket", target: "≥ 20" },
];

function severityTone(s: string) {
  if (s === "CRITICAL") return "critical" as const;
  if (s === "WARN") return "warn" as const;
  return "watch" as const;
}

function statusTone(s: string) {
  if (s === "CHAMPION") return "champion" as const;
  if (s === "CHALLENGER") return "challenger" as const;
  if (s === "RETIRED") return "retired" as const;
  return "info" as const;
}

export default async function ModelsPage() {
  /**
   * Calibration source: the repo ships NO calibration map for ARGUS-EQ-1.0.
   * The only shipped isotonic map is calibration_ARGUS-EQ-1.1_h20.json
   * (21 knots, n_oof=1008, brier 0.248873 -> 0.234713), so we request the
   * version that actually has data and label it in the UI. If the API still
   * answers empty/MISSING we say "no curve available" instead of drawing a
   * blank axis.
   */
  const calibVersion = "ARGUS-EQ-1.1";
  const horizon = 20;
  const [models, calib, flags] = await Promise.all([
    api.getModels(),
    api.getCalibration(calibVersion, horizon),
    api.getDegradationFlags(),
  ]);
  const prov = provenanceOf(models.meta, calib.meta, flags.meta);
  const champion =
    models.data.find((m) => m.status === "CHAMPION") ?? models.data[0];
  const buckets = calib.data;

  // Metric fields are optional on the live API: a missing value renders "—".
  const metrics = [
    { label: "Brier score (20d)", value: metric3(champion?.brier) },
    { label: "ROC-AUC (20d)", value: metric3(champion?.roc_auc) },
    {
      label: "Calibration error (20d)",
      value: metric3(champion?.cal_error),
    },
    { label: "OOS predictions tracked", value: count0(champion?.n) },
  ];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl tracking-[0.08em] text-stone-100">
            AI Models — Observatory
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Calibration beats accuracy. Every claim needs ≥ 20 samples per
            bucket, ≥ 100 for published claims. As-of{" "}
            {fmtDateTime(models.meta.as_of)} UTC.
          </p>
        </div>
        {prov.demo && <DemoBadge asOf={prov.asOf} error={prov.error} />}
      </div>

      <DataErrorBanner error={prov.error} empty={prov.empty && !prov.demo} />

      {!champion && (
        <p className="text-sm text-slate-500">
          No model version is registered in the API — no champion metrics to
          show.
        </p>
      )}

      {/* Metric cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {champion &&
          metrics.map((m) => (
            <Card key={m.label}>
              <p className="text-xs uppercase tracking-wider text-slate-500">
                {m.label}
              </p>
              <p className="mt-2 font-mono text-3xl font-bold text-white">
                {m.value}
              </p>
              {prov.demo && <SyntheticTag className="mt-2" />}
              {m.value === "—" && (
                <p className="mt-1 text-xs text-slate-500">
                  not reported by the API
                </p>
              )}
            </Card>
          ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Calibration curve */}
        <Card
          title="Reliability curve — 20-day P(up)"
          subtitle={`${calibVersion} · isotonic map · status ${calib.meta.data_status} · observed frequency vs predicted probability per bucket`}
        >
          {prov.demo && <SyntheticTag className="mb-2" />}
          {buckets.length === 0 && (
            <p className="mb-2 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
              No calibration curve available for {calibVersion} at horizon{" "}
              {horizon}d (data_status {calib.meta.data_status}). The API returned
              no buckets — no curve is drawn rather than a fabricated one.
            </p>
          )}
          <CalibrationChart buckets={buckets} />
          <Table
            className="mt-4"
            head={["Bucket", "Predicted", "Observed", "n"]}
          >
            {buckets.map((b, i) => (
              <tr key={i} className="hover:bg-ink-800/50">
                <Td className="font-mono text-slate-400">{i + 1}</Td>
                <Td className="font-mono text-slate-200">
                  {pct(b.predicted)}
                </Td>
                <Td className="font-mono text-bronze-300">
                  {pct(b.observed)}
                </Td>
                <Td className="font-mono text-slate-400">
                  {count0(b.n)}
                </Td>
              </tr>
            ))}
            {buckets.length === 0 && (
              <tr>
                <Td className="text-slate-500" >
                  No calibration buckets returned by the API.
                </Td>
              </tr>
            )}
          </Table>
        </Card>

        {/* Registry */}
        <Card
          title="Model registry"
          subtitle="Champion / challenger status per MODEL_EVALUATION.md §4"
        >
          {prov.demo && <SyntheticTag className="mb-2" />}
          <Table head={["Version", "Status", "Horizons", "Brier", "AUC", "n"]}>
            {models.data.map((m) => (
              <tr key={m.version} className="hover:bg-ink-800/50">
                <Td className="font-mono font-semibold text-white">
                  {m.version}
                </Td>
                <Td>
                  <Badge tone={statusTone(m.status)}>{m.status}</Badge>
                </Td>
                <Td className="font-mono text-xs text-slate-400">
                  {m.horizons && m.horizons.length > 0
                    ? `${m.horizons.length}h`
                    : "—"}
                </Td>
                <Td className="font-mono text-slate-200">
                  {metric3(m.brier)}
                </Td>
                <Td className="font-mono text-slate-200">
                  {metric3(m.roc_auc)}
                </Td>
                <Td className="font-mono text-slate-400">{count0(m.n)}</Td>
              </tr>
            ))}
            {models.data.length === 0 && (
              <tr>
                <Td className="text-slate-500">
                  No model versions returned by the API.
                </Td>
              </tr>
            )}
          </Table>
          <div className="mt-5 border-t border-ink-800 pt-4">
            <h3 className="mb-2 text-sm font-semibold text-slate-300">
              Engineering targets (not promises)
            </h3>
            <ul className="space-y-1">
              {TARGETS.map((t) => (
                <li key={t.metric} className="text-sm text-slate-400">
                  {t.metric}:{" "}
                  <span className="font-mono text-slate-200">{t.target}</span>
                </li>
              ))}
            </ul>
          </div>
        </Card>
      </div>

      {/* Degradation flags */}
      <Card
        title="Degradation flags"
        subtitle="Rolling 63-day metrics vs first-90-day baseline · WATCH → WARN → CRITICAL"
      >
        {prov.demo && <SyntheticTag className="mb-2" />}
        <Table
          head={[
            "Detected",
            "Model",
            "Horizon",
            "Metric",
            "Baseline",
            "Current",
            "Severity",
          ]}
        >
          {flags.data.map((f, i) => (
            <tr key={i} className="hover:bg-ink-800/50">
              <Td className="font-mono text-xs text-slate-400">
                {fmtDateTime(f.detected_at)}
              </Td>
              <Td className="font-mono text-slate-200">
                {f.model_version ?? "—"}
              </Td>
              <Td className="text-slate-300">
                {typeof f.horizon === "number" ? `${f.horizon}d` : "—"}
              </Td>
              <Td className="font-mono text-xs text-slate-300">
                {f.metric ?? "—"}
              </Td>
              <Td className="font-mono text-slate-400">
                {metric3(f.baseline)}
              </Td>
              <Td className="font-mono text-white">{metric3(f.current)}</Td>
              <Td>
                <Badge tone={severityTone(f.severity)}>{f.severity}</Badge>
              </Td>
            </tr>
          ))}
          {flags.data.length === 0 && (
            <tr>
              <Td className="text-slate-500">
                No degradation flags recorded.
              </Td>
            </tr>
          )}
        </Table>
        <p className="mt-4 text-xs italic text-slate-500">{DISCLAIMER}</p>
      </Card>
    </div>
  );
}
