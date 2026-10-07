export const dynamic = "force-static";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, Td } from "@/components/ui/table";
import { DemoBadge } from "@/components/DemoBadge";
import { RegimeChart } from "@/components/charts/RegimeChart";
import { DISCLAIMER, fmtDateTime, pct } from "@/lib/format";

function tone(regime: string) {
  const r = regime.toLowerCase();
  if (r.includes("bull")) return "bull" as const;
  if (r.includes("bear")) return "bear" as const;
  if (r.includes("vol")) return "warn" as const;
  return "neutral" as const;
}

export default async function MarketRegimePage() {
  const [env, hist] = await Promise.all([
    api.getRegime(),
    api.getRegimeHistory(365),
  ]);
  const r = env.data;
  const rawHistory = hist.meta.demo ? r.history : hist.data.history;
  // Normalize confidence to 0–1 (CSV/API use 0–100, demo regime uses 0–1).
  const history = rawHistory.map((h: { time: string; regime: string; confidence: number; data_status?: string }) => ({
    time: h.time,
    regime: h.regime,
    confidence: h.confidence > 1 ? h.confidence / 100 : h.confidence,
    data_status: (h as { data_status?: string }).data_status ?? "UNCONFIRMED",
  }));
  const histDemo = hist.meta.demo;
  const latest = history[history.length - 1];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Market Regime</h1>
          <p className="mt-1 text-sm text-slate-500">
            Regime-conditioned ensemble weights · as-of{" "}
            {fmtDateTime(env.meta.as_of)} UTC · status {env.meta.data_status}
          </p>
        </div>
        {env.meta.demo && <DemoBadge asOf={env.meta.as_of} />}
      </div>

      {!histDemo && latest && (
        <Card
          title="Classified history — real data"
          subtitle={`1,698 daily rows 2020→${latest.time} · rule-based classifier · row-level data_status in table`}
        >
          <div className="flex items-center gap-4">
            <Badge tone={tone(latest.regime)} className="text-sm">
              {latest.regime}
            </Badge>
            <p className="text-sm text-slate-400">
              Latest ({latest.time}) · status{" "}
              <Badge tone={latest.data_status === "CONFIRMED" ? "bull" : "neutral"}>
                {latest.data_status}
              </Badge>
            </p>
          </div>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Current regime" subtitle="Classified by the E engine">
          <div className="flex items-center gap-4">
            <Badge tone={tone(r.regime)} className="text-sm">
              {r.regime}
            </Badge>
            <p className="text-sm text-slate-400">
              Confidence{" "}
              <span className="font-mono font-semibold text-white">
                {pct(r.confidence)}
              </span>
            </p>
          </div>
          <p className="mt-4 text-sm text-slate-400">
            Regime confidence measures the quality of the classification setup —
            it is not a probability of future returns. Ensemble weights shift
            with regime: trend-following engines carry more weight in Bull,
            defensive factors in Bear, volatility harvesting in HighVol.
          </p>
        </Card>

        <Card
          title="Drivers"
          subtitle="Evidence behind the classification"
          className="lg:col-span-2"
        >
          <ul className="space-y-3">
            {r.drivers.map((d, i) => (
              <li key={i} className="flex gap-3 text-sm text-slate-300">
                <span className="font-mono text-bronze-400">
                  {String(i + 1).padStart(2, "0")}
                </span>
                {d}
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <Card
        title="Regime history — 12 months"
        subtitle="Daily classification with confidence (bar color = regime)"
      >
        <RegimeChart history={history.slice(-180)} />
      </Card>

      <Card title="Recent regime spells" subtitle="Latest 10 daily rows">
        <Table head={["Date", "Regime", "Confidence", "Status"]}>
          {[...history].reverse().slice(0, 10).map((h) => (
            <tr key={h.time} className="hover:bg-ink-800/50">
              <Td className="font-mono text-xs text-slate-400">{h.time}</Td>
              <Td>
                <Badge tone={tone(h.regime)}>{h.regime}</Badge>
              </Td>
              <Td className="font-mono text-slate-200">{pct(h.confidence)}</Td>
              <Td>
                <Badge tone="neutral">{h.data_status}</Badge>
              </Td>
            </tr>
          ))}
        </Table>
        <p className="mt-4 text-xs italic text-slate-500">{DISCLAIMER}</p>
      </Card>
    </div>
  );
}
