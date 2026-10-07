import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { HORIZON_LABELS, HORIZONS } from "@/lib/demo-data";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, Td } from "@/components/ui/table";
import { DemoBadge } from "@/components/DemoBadge";
import { PriceChart, type ForwardBand } from "@/components/charts/PriceChart";
import {
  DISCLAIMER,
  fmtDateTime,
  money,
  pct,
  ret1,
  ratio1,
  score0,
} from "@/lib/format";
import type { Prediction } from "@/lib/types";

function ScoreBar({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="text-slate-400">{label}</span>
        <span className="font-mono font-semibold text-white">
          {score0(value)}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-ink-700">
        <div
          className="h-full rounded-full bg-sky-500"
          style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
        />
      </div>
    </div>
  );
}

function ExplanationList({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "reasons" | "risks" | "invalid";
}) {
  const color =
    tone === "reasons"
      ? "text-emerald-400"
      : tone === "risks"
        ? "text-amber-400"
        : "text-rose-400";
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-slate-300">{title}</h3>
      <ul className="space-y-1.5">
        {items.map((it, i) => (
          <li key={i} className="text-sm text-slate-400">
            <span className={`mr-2 ${color}`}>
              {tone === "reasons" ? "▲" : tone === "risks" ? "◆" : "✕"}
            </span>
            {it}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function generateStaticParams() {
  // Pre-rendered tickers for `output: export`. The page also works for any
  // other valid ticker at runtime via client-side navigation.
  const tickers = ["SPY", "QQQ", "NVDA", "AAPL", "MSFT", "TSLA", "AMZN",
    "META", "GOOGL", "AMD", "NFLX", "CRM", "ORCL", "AVGO", "COST"];
  return tickers.map((ticker) => ({ ticker }));
}

export default async function StockPage({
  params,
}: {
  params: { ticker: string };
}) {
  const ticker = params.ticker.toUpperCase();
  if (!/^[A-Z.\-^]{1,10}$/.test(ticker)) notFound();

  const [asset, prices, preds] = await Promise.all([
    api.getAsset(ticker),
    api.getPrices(ticker),
    api.getPredictions(ticker, HORIZONS),
  ]);
  const isDemo = asset.meta.demo || prices.meta.demo || preds.meta.demo;

  const a = asset.data;
  const p20: Prediction | undefined = preds.data.find((p) => p.horizon === 20);
  const chgUp = a.change >= 0;

  // Forward prediction band for the chart: expected path ± vol, 20 sessions
  const band: ForwardBand[] = Array.from({ length: 20 }, (_, i) => {
    const frac = (i + 1) / 20;
    const er = p20 ? p20.expected_return * frac : null;
    const w = p20 ? p20.expected_vol * Math.sqrt(frac) * 0.9 : null;
    return {
      label: `+${i + 1}d`,
      expected: er,
      high: er === null || w === null ? null : er + w,
      low: er === null || w === null ? null : er - w,
    };
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link
          href="/market-scanner"
          className="text-sm text-slate-500 hover:text-slate-300"
        >
          ← Scanner
        </Link>
        {isDemo && <DemoBadge asOf={asset.meta.as_of} />}
      </div>

      {/* Header */}
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-3xl font-bold text-white">{a.ticker}</h1>
              <Badge tone={chgUp ? "bull" : "bear"}>
                {chgUp ? "+" : ""}
                {ret1(a.change_pct)}
              </Badge>
              <Badge>{a.data_status}</Badge>
            </div>
            <p className="mt-1 text-sm text-slate-400">{a.name}</p>
            <p className="mt-1 text-xs text-slate-500">
              {a.exchange} · {a.sector} · {a.industry} · as-of{" "}
              {fmtDateTime(asset.meta.as_of)} UTC
            </p>
          </div>
          <div className="text-right">
            <p className="font-mono text-3xl font-bold text-white">
              ${a.price.toFixed(2)}
            </p>
            <p className={`text-sm ${chgUp ? "text-emerald-300" : "text-rose-300"}`}>
              {chgUp ? "+" : ""}
              {a.change.toFixed(2)} ({ret1(a.change_pct)})
            </p>
            <p className="mt-1 text-xs text-slate-500">
              Mkt cap {money(a.mkt_cap)}
            </p>
          </div>
        </div>
      </Card>

      {p20 && (
        <div className="grid gap-6 lg:grid-cols-3">
          {/* AI score panel */}
          <Card
            title="AI score — 20 day horizon"
            subtitle={`Model ${p20.model_version} · regime ${p20.regime}`}
          >
            <div className="mb-5 flex items-center gap-5">
              <div className="flex h-24 w-24 items-center justify-center rounded-full border-4 border-sky-500">
                <span className="font-mono text-3xl font-bold text-white">
                  {score0(p20.composite_score)}
                </span>
              </div>
              <div className="space-y-1 text-sm">
                <p className="text-slate-300">
                  P(up){" "}
                  <span className="font-mono font-semibold text-emerald-300">
                    {pct(p20.p_positive)}
                  </span>
                </p>
                <p className="text-slate-300">
                  P(down){" "}
                  <span className="font-mono font-semibold text-rose-300">
                    {pct(p20.p_negative)}
                  </span>
                </p>
                <p className="text-slate-300">
                  E[R]{" "}
                  <span className="font-mono font-semibold text-white">
                    {ret1(p20.expected_return)}
                  </span>
                </p>
                <p className="text-xs text-slate-500">
                  Confidence (setup quality): {score0(p20.confidence)}/100
                </p>
              </div>
            </div>
            <div className="space-y-3">
              <ScoreBar label="Trend strength" value={p20.trend_strength} />
              <ScoreBar label="Momentum" value={p20.momentum_score} />
              <ScoreBar label="Fundamental" value={p20.fundamental_score} />
              <ScoreBar label="Risk" value={p20.risk_score} />
              <ScoreBar label="Volatility" value={p20.volatility_score} />
            </div>
          </Card>

          {/* Price chart */}
          <Card
            title="Price — 12 months + 20d prediction band"
            subtitle="Close with MA 50 / MA 200 overlays"
            className="lg:col-span-2"
          >
            <PriceChart
              bars={prices.data}
              band={band}
              currentPrice={a.price}
            />
          </Card>
        </div>
      )}

      {/* Multi-horizon table */}
      <Card
        title="Multi-horizon predictions"
        subtitle={`Locked records · ${preds.data[0]?.model_version ?? "—"} · as-of ${fmtDateTime(preds.meta.as_of)} UTC`}
      >
        <Table
          head={[
            "Horizon",
            "P(up)",
            "P(down)",
            "E[R]",
            "σ",
            "CI 90%",
            "Downside",
            "Upside",
            "R/R",
            "Score",
            "Conf",
          ]}
        >
          {preds.data.map((p) => (
            <tr
              key={p.horizon}
              className={`hover:bg-ink-800/50 ${
                p.horizon === 20 ? "bg-sky-600/5" : ""
              }`}
            >
              <Td className="font-medium text-slate-200">
                {HORIZON_LABELS[p.horizon] ?? `${p.horizon}d`}
              </Td>
              <Td className="font-mono text-emerald-300">{pct(p.p_positive)}</Td>
              <Td className="font-mono text-rose-300">{pct(p.p_negative)}</Td>
              <Td className="font-mono text-white">{ret1(p.expected_return)}</Td>
              <Td className="font-mono text-slate-300">{pct(p.expected_vol)}</Td>
              <Td className="font-mono text-xs text-slate-400">
                [{ret1(p.ci[0])}, {ret1(p.ci[1])}]
              </Td>
              <Td className="font-mono text-slate-300">
                {ret1(-p.downside_risk)}
              </Td>
              <Td className="font-mono text-slate-300">
                {ret1(p.upside_potential)}
              </Td>
              <Td className="font-mono text-slate-200">{ratio1(p.risk_reward)}</Td>
              <Td className="font-mono font-semibold text-white">
                {score0(p.composite_score)}
              </Td>
              <Td className="font-mono text-slate-300">{score0(p.confidence)}</Td>
            </tr>
          ))}
        </Table>
        <p className="mt-4 text-xs italic text-slate-500">{DISCLAIMER}</p>
      </Card>

      {/* Engine breakdown — 8 engines */}
      {p20?.engine_signals && (
        <Card
          title="Engine breakdown — 8 engines"
          subtitle={`Pipeline: ${p20.pipeline ?? "synthetic_stub"} · calibrated: ${p20.calibrated ? "yes (ARGUS-EQ-1.1 isotonic)" : "no"} · as-of ${fmtDateTime(p20.as_of)} UTC`}
        >
          <Table head={["Engine", "Signal", "Confidence", "Data status", "Note"]}>
            {Object.entries(p20.engine_signals).map(([name, e]) => (
              <tr key={name} className="hover:bg-ink-800/50">
                <Td className="font-medium text-slate-200">{name}</Td>
                <Td>
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 w-20 overflow-hidden rounded-full bg-ink-700">
                      <div
                        className={`h-full rounded-full ${e.signal >= 0 ? "bg-emerald-500" : "bg-rose-500"}`}
                        style={{
                          width: `${Math.abs(e.signal) * 100}%`,
                          marginLeft: e.signal >= 0 ? "50%" : `${50 - Math.abs(e.signal) * 50}%`,
                        }}
                      />
                    </div>
                    <span
                      className={`font-mono text-xs ${e.signal >= 0 ? "text-emerald-300" : "text-rose-300"}`}
                    >
                      {e.signal >= 0 ? "+" : ""}
                      {e.signal.toFixed(2)}
                    </span>
                  </div>
                </Td>
                <Td className="font-mono text-slate-300">{score0(e.confidence * 100)}</Td>
                <Td>
                  <Badge
                    tone={
                      e.data_status === "CONFIRMED"
                        ? "bull"
                        : e.data_status === "MISSING"
                          ? "bear"
                          : "neutral"
                    }
                  >
                    {e.data_status}
                  </Badge>
                </Td>
                <Td className="max-w-md text-xs text-slate-500">{e.note || "—"}</Td>
              </tr>
            ))}
          </Table>
          <p className="mt-4 text-xs text-slate-500">
            A/B/H feed the calibrated probability via pinned ARGUS-EQ-1.1
            regime weights. C/D/F/G are overlay signals (prior-weighted in the
            composite score) — they do not enter the calibrated probability
            until validated out-of-sample.
          </p>
        </Card>
      )}

      {/* Explanation */}
      {p20 && (
        <Card
          title="Explanation — why this prediction, and what would change it"
          subtitle={`Generated at lock time · ${fmtDateTime(p20.as_of)} UTC`}
        >
          <div className="grid gap-6 md:grid-cols-3">
            <ExplanationList
              title="Reasons (bullish evidence)"
              items={p20.explanation.reasons}
              tone="reasons"
            />
            <ExplanationList
              title="Risks (devil's advocate)"
              items={p20.explanation.risks}
              tone="risks"
            />
            <ExplanationList
              title="Invalidated if"
              items={p20.explanation.invalidated_if}
              tone="invalid"
            />
          </div>
          <p className="mt-5 border-t border-ink-800 pt-4 text-xs italic text-slate-500">
            {p20.disclaimer}
          </p>
        </Card>
      )}
    </div>
  );
}
