"use client";

import { useState } from "react";
import { api, DEFAULT_BACKTEST_CONFIG } from "@/lib/api";
import type { BacktestConfig, BacktestResult } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, Td } from "@/components/ui/table";
import { DemoBadge } from "@/components/DemoBadge";
import { EquityChart } from "@/components/charts/EquityChart";
import { DISCLAIMER, pct, ret1 } from "@/lib/format";

const UNIVERSES = ["SP500", "NASDAQ100", "RUSSELL2000", "SECTOR_TECH", "SP500_VALUE"];

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium uppercase tracking-wider text-slate-500">
        {label}
      </span>
      {children}
    </label>
  );
}

const inputCls =
  "w-full rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-slate-200 focus:border-bronze-500 focus:outline-none";

export default function BacktestingPage() {
  const [cfg, setCfg] = useState<BacktestConfig>(DEFAULT_BACKTEST_CONFIG);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [running, setRunning] = useState(false);

  const set = (k: keyof BacktestConfig, v: string | number | boolean) =>
    setCfg((c) => ({ ...c, [k]: v }));

  async function run() {
    setRunning(true);
    try {
      const r = await api.runBacktest(cfg);
      setResult(r);
    } finally {
      setRunning(false);
    }
  }

  const m = result?.metrics;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl tracking-[0.08em] text-stone-100">Backtesting</h1>
          <p className="mt-1 text-sm text-slate-500">
            Event-driven, with slippage and costs, benchmarked vs SPY. Research
            store only — backtests never touch production records.
          </p>
        </div>
        {result && <DemoBadge />}
      </div>

      <Card
        title="Strategy configuration"
        subtitle="Every backtest declares universe, dates, rules, costs and sizing"
      >
        <div className="grid gap-4 md:grid-cols-3 lg:grid-cols-4">
          <Field label="Strategy name">
            <input
              className={inputCls}
              value={cfg.name}
              onChange={(e) => set("name", e.target.value)}
            />
          </Field>
          <Field label="Universe">
            <select
              className={inputCls}
              value={cfg.universe}
              onChange={(e) => set("universe", e.target.value)}
            >
              {UNIVERSES.map((u) => (
                <option key={u} value={u}>
                  {u}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Start date">
            <input
              type="date"
              className={inputCls}
              value={cfg.start}
              onChange={(e) => set("start", e.target.value)}
            />
          </Field>
          <Field label="End date">
            <input
              type="date"
              className={inputCls}
              value={cfg.end}
              onChange={(e) => set("end", e.target.value)}
            />
          </Field>
          <Field label="Stop loss (%)">
            <input
              type="number"
              step="0.5"
              className={inputCls}
              value={cfg.stop_loss}
              onChange={(e) => set("stop_loss", Number(e.target.value))}
            />
          </Field>
          <Field label="Take profit (%)">
            <input
              type="number"
              step="0.5"
              className={inputCls}
              value={cfg.take_profit}
              onChange={(e) => set("take_profit", Number(e.target.value))}
            />
          </Field>
          <Field label="Slippage (bps)">
            <input
              type="number"
              step="1"
              className={inputCls}
              value={cfg.slippage_bps}
              onChange={(e) => set("slippage_bps", Number(e.target.value))}
            />
          </Field>
          <Field label="Commission ($/trade)">
            <input
              type="number"
              step="0.1"
              className={inputCls}
              value={cfg.commission}
              onChange={(e) => set("commission", Number(e.target.value))}
            />
          </Field>
        </div>
        <div className="mt-4 flex items-center gap-6">
          <label className="flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={cfg.allow_short}
              onChange={(e) => set("allow_short", e.target.checked)}
              className="h-4 w-4 accent-bronze-600"
            />
            Allow short
          </label>
          <Button onClick={run} disabled={running}>
            {running ? "Running…" : "Run backtest"}
          </Button>
          {result && <Badge tone="demo">Demo result · synthetic</Badge>}
        </div>
      </Card>

      {result && m && (
        <>
          <Card
            title={`Equity curve — ${result.config.name}`}
            subtitle={`${result.config.universe} · ${result.config.start} → ${result.config.end} · net of costs`}
          >
            <EquityChart equity={result.equity} />
          </Card>

          <Card title="Metrics" subtitle="Full set — no cherry-picking">
            <Table head={["Metric", "Value", "Metric", "Value"]}>
              <tr className="hover:bg-ink-800/50">
                <Td className="text-slate-400">CAGR</Td>
                <Td className="font-mono font-semibold text-white">
                  {ret1(m.cagr)}
                </Td>
                <Td className="text-slate-400">Total return</Td>
                <Td className="font-mono text-slate-200">
                  {ret1(m.total_return)}
                </Td>
              </tr>
              <tr className="hover:bg-ink-800/50">
                <Td className="text-slate-400">Sharpe</Td>
                <Td className="font-mono font-semibold text-white">
                  {m.sharpe.toFixed(2)}
                </Td>
                <Td className="text-slate-400">Sortino</Td>
                <Td className="font-mono text-slate-200">
                  {m.sortino.toFixed(2)}
                </Td>
              </tr>
              <tr className="hover:bg-ink-800/50">
                <Td className="text-slate-400">Max drawdown</Td>
                <Td className="font-mono text-rose-300">{ret1(m.max_drawdown)}</Td>
                <Td className="text-slate-400">Win rate</Td>
                <Td className="font-mono text-slate-200">{pct(m.win_rate)}</Td>
              </tr>
              <tr className="hover:bg-ink-800/50">
                <Td className="text-slate-400">Profit factor</Td>
                <Td className="font-mono text-slate-200">
                  {m.profit_factor.toFixed(2)}
                </Td>
                <Td className="text-slate-400">Expectancy / month</Td>
                <Td className="font-mono text-slate-200">
                  {ret1(m.expectancy)}
                </Td>
              </tr>
              <tr className="hover:bg-ink-800/50">
                <Td className="text-slate-400">Trades</Td>
                <Td className="font-mono text-slate-200">
                  {m.n_trades.toLocaleString()}
                </Td>
                <Td className="text-slate-400">Exposure</Td>
                <Td className="font-mono text-slate-200">{pct(m.exposure)}</Td>
              </tr>
              <tr className="hover:bg-ink-800/50">
                <Td className="text-slate-400">Alpha (vs SPY)</Td>
                <Td className="font-mono text-slate-200">{ret1(m.alpha)}</Td>
                <Td className="text-slate-400">Beta</Td>
                <Td className="font-mono text-slate-200">
                  {m.beta.toFixed(2)}
                </Td>
              </tr>
            </Table>
            <p className="mt-4 text-xs italic text-slate-500">{DISCLAIMER}</p>
          </Card>
        </>
      )}
    </div>
  );
}
