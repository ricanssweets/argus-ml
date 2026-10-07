export const dynamic = "force-static";
import Link from "next/link";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, Td } from "@/components/ui/table";
import { DemoBadge } from "@/components/DemoBadge";
import { DISCLAIMER, fmtDateTime, pct, ret1, score0 } from "@/lib/format";

function regimeTone(regime: string) {
  const r = regime.toLowerCase();
  if (r.includes("bull")) return "bull" as const;
  if (r.includes("bear")) return "bear" as const;
  return "neutral" as const;
}

export default async function DashboardPage() {
  const [regime, scanner, models, locked] = await Promise.all([
    api.getRegime(),
    api.getScanner(8),
    api.getModels(),
    api.getLockedToday(),
  ]);
  const isDemo =
    regime.meta.demo || scanner.meta.demo || locked.meta.demo;

  const champion = models.data.find((m) => m.status === "CHAMPION");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Dashboard</h1>
          <p className="mt-1 text-sm text-slate-500">
            Data as-of {fmtDateTime(regime.meta.as_of)} UTC · status{" "}
            {regime.meta.data_status}
          </p>
        </div>
        {isDemo && <DemoBadge asOf={regime.meta.as_of} />}
      </div>

      {/* Market regime banner */}
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <Badge tone={regimeTone(regime.data.regime)}>
              {regime.data.regime}
            </Badge>
            <div>
              <p className="text-lg font-semibold text-white">
                Market regime: {regime.data.regime}
              </p>
              <p className="text-sm text-slate-400">
                Confidence {pct(regime.data.confidence)} — separate from any
                outcome probability.
              </p>
            </div>
          </div>
          <Link
            href="/market-regime"
            className="text-sm font-medium text-sky-400 hover:text-sky-300"
          >
            Regime detail →
          </Link>
        </div>
        <ul className="mt-4 grid gap-2 md:grid-cols-2">
          {regime.data.drivers.map((d, i) => (
            <li key={i} className="text-sm text-slate-400">
              <span className="mr-2 text-sky-400">▸</span>
              {d}
            </li>
          ))}
        </ul>
      </Card>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Top opportunities */}
        <Card
          title="Top opportunities"
          subtitle="20-day horizon · ranked by composite score"
          className="lg:col-span-2"
          action={
            <Link
              href="/market-scanner"
              className="text-sm font-medium text-sky-400 hover:text-sky-300"
            >
              Scanner →
            </Link>
          }
        >
          <Table
            head={[
              "Ticker",
              "Setup",
              "Score",
              "P(up)",
              "E[R]",
              "R/R",
            ]}
          >
            {scanner.data.map((s) => (
              <tr key={s.ticker} className="hover:bg-ink-800/50">
                <Td>
                  <Link
                    href={`/stock/${s.ticker}`}
                    className="font-semibold text-sky-400 hover:text-sky-300"
                  >
                    {s.ticker}
                  </Link>
                  <p className="text-xs text-slate-500">{s.name}</p>
                </Td>
                <Td className="font-mono text-xs text-slate-400">
                  {s.setup.replaceAll("_", " ")}
                </Td>
                <Td className="font-semibold text-white">{score0(s.score)}</Td>
                <Td className="text-emerald-300">{pct(s.p_positive)}</Td>
                <Td className="text-slate-200">{ret1(s.expected_return)}</Td>
                <Td className="text-slate-200">
                  {(Math.round(s.risk_reward * 10) / 10).toFixed(1)}x
                </Td>
              </tr>
            ))}
          </Table>
        </Card>

        {/* Model health */}
        <Card
          title="Model health"
          subtitle={champion ? `Champion: ${champion.version}` : "Observatory"}
          action={
            <Link
              href="/models"
              className="text-sm font-medium text-sky-400 hover:text-sky-300"
            >
              Observatory →
            </Link>
          }
        >
          {champion ? (
            <dl className="space-y-3">
              {[
                ["Brier score (20d)", champion.brier.toFixed(3), "target < 0.22"],
                ["ROC-AUC (20d)", champion.roc_auc.toFixed(3), "target > 0.56"],
                [
                  "Calibration error",
                  champion.cal_error.toFixed(3),
                  "target < 0.04",
                ],
                [
                  "OOS samples",
                  champion.n.toLocaleString(),
                  "min 100 per claim",
                ],
              ].map(([label, value, target]) => (
                <div
                  key={label as string}
                  className="flex items-center justify-between border-b border-ink-800 pb-2.5 last:border-0"
                >
                  <div>
                    <dt className="text-sm text-slate-300">{label}</dt>
                    <dd className="text-xs text-slate-500">{target}</dd>
                  </div>
                  <span className="font-mono text-lg font-semibold text-white">
                    {value}
                  </span>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-sm text-slate-500">No champion model found.</p>
          )}
        </Card>
      </div>

      {/* Today's locked predictions */}
      <Card
        title="Today's locked predictions"
        subtitle="Immutable once locked — new model versions apply to new predictions only"
        action={
          <Link
            href="/predictions"
            className="text-sm font-medium text-sky-400 hover:text-sky-300"
          >
            All predictions →
          </Link>
        }
      >
        <Table
          head={[
            "Ticker",
            "Horizon",
            "P(up)",
            "E[R]",
            "Score",
            "Confidence",
            "Model",
          ]}
        >
          {locked.data.map((p) => (
            <tr key={`${p.ticker}-${p.horizon}`} className="hover:bg-ink-800/50">
              <Td>
                <Link
                  href={`/stock/${p.ticker}`}
                  className="font-semibold text-sky-400 hover:text-sky-300"
                >
                  {p.ticker}
                </Link>
              </Td>
              <Td className="text-slate-300">{p.horizon}d</Td>
              <Td className="text-emerald-300">{pct(p.p_positive)}</Td>
              <Td className="text-slate-200">{ret1(p.expected_return)}</Td>
              <Td className="font-semibold text-white">
                {score0(p.composite_score)}
              </Td>
              <Td className="text-slate-300">{score0(p.confidence)}</Td>
              <Td className="font-mono text-xs text-slate-400">
                {p.model_version}
              </Td>
            </tr>
          ))}
        </Table>
        <p className="mt-4 text-xs italic text-slate-500">{DISCLAIMER}</p>
      </Card>

      <p className="text-xs text-slate-600">
        Portfolio snapshot (Phase 5) will appear here once positions are
        imported. No real positions are connected yet.
      </p>
    </div>
  );
}
