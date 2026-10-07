import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, Td } from "@/components/ui/table";
import { DemoBadge } from "@/components/DemoBadge";
import { CalibrationChart } from "@/components/charts/CalibrationChart";
import { DISCLAIMER, fmtDateTime, pct } from "@/lib/format";

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
  const version = "ARGUS-EQ-1.0";
  const horizon = 20;
  const [models, calib, flags] = await Promise.all([
    api.getModels(),
    api.getCalibration(version, horizon),
    api.getDegradationFlags(),
  ]);
  const isDemo = models.meta.demo || calib.meta.demo || flags.meta.demo;
  const champion = models.data.find((m) => m.status === "CHAMPION");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">
            AI Models — Observatory
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Calibration beats accuracy. Every claim needs ≥ 20 samples per
            bucket, ≥ 100 for published claims. As-of{" "}
            {fmtDateTime(models.meta.as_of)} UTC.
          </p>
        </div>
        {isDemo && <DemoBadge asOf={models.meta.as_of} />}
      </div>

      {/* Metric cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {champion &&
          [
            { label: "Brier score (20d)", value: champion.brier.toFixed(3) },
            { label: "ROC-AUC (20d)", value: champion.roc_auc.toFixed(3) },
            {
              label: "Calibration error (20d)",
              value: champion.cal_error.toFixed(3),
            },
            {
              label: "OOS predictions tracked",
              value: champion.n.toLocaleString(),
            },
          ].map((m) => (
            <Card key={m.label}>
              <p className="text-xs uppercase tracking-wider text-slate-500">
                {m.label}
              </p>
              <p className="mt-2 font-mono text-3xl font-bold text-white">
                {m.value}
              </p>
            </Card>
          ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Calibration curve */}
        <Card
          title="Reliability curve — 20-day P(up)"
          subtitle={`${version} · observed frequency vs predicted probability per bucket`}
        >
          <CalibrationChart buckets={calib.data} />
          <Table
            className="mt-4"
            head={["Bucket", "Predicted", "Observed", "n"]}
          >
            {calib.data.map((b, i) => (
              <tr key={i} className="hover:bg-ink-800/50">
                <Td className="font-mono text-slate-400">{i + 1}</Td>
                <Td className="font-mono text-slate-200">{pct(b.predicted)}</Td>
                <Td className="font-mono text-sky-300">{pct(b.observed)}</Td>
                <Td className="font-mono text-slate-400">
                  {b.n.toLocaleString()}
                </Td>
              </tr>
            ))}
          </Table>
        </Card>

        {/* Registry */}
        <Card
          title="Model registry"
          subtitle="Champion / challenger status per MODEL_EVALUATION.md §4"
        >
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
                  {m.horizons.length}h
                </Td>
                <Td className="font-mono text-slate-200">
                  {m.brier.toFixed(3)}
                </Td>
                <Td className="font-mono text-slate-200">
                  {m.roc_auc.toFixed(3)}
                </Td>
                <Td className="font-mono text-slate-400">
                  {m.n.toLocaleString()}
                </Td>
              </tr>
            ))}
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
              <Td className="font-mono text-slate-200">{f.model_version}</Td>
              <Td className="text-slate-300">{f.horizon}d</Td>
              <Td className="font-mono text-xs text-slate-300">{f.metric}</Td>
              <Td className="font-mono text-slate-400">
                {f.baseline.toFixed(3)}
              </Td>
              <Td className="font-mono text-white">{f.current.toFixed(3)}</Td>
              <Td>
                <Badge tone={severityTone(f.severity)}>{f.severity}</Badge>
              </Td>
            </tr>
          ))}
        </Table>
        <p className="mt-4 text-xs italic text-slate-500">{DISCLAIMER}</p>
      </Card>
    </div>
  );
}
