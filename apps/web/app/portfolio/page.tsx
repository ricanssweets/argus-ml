"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, Td } from "@/components/ui/table";
import { DemoBadge } from "@/components/DemoBadge";
import { DISCLAIMER, pct, ret1, ratio1 } from "@/lib/format";
import type { PortfolioAnalysis, PortfolioPosition } from "@/lib/types";

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-ink-800/60 p-3">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-1 font-mono text-lg font-semibold text-white">{value}</p>
    </div>
  );
}

export default function PortfolioPage() {
  const [text, setText] = useState("NVDA:10\nAAPL:10\nMSFT:10");
  const [result, setResult] = useState<{
    data: PortfolioAnalysis;
    demo: boolean;
    as_of: string;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function analyze() {
    setLoading(true);
    setError(null);
    try {
      const positions: PortfolioPosition[] = text
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean)
        .map((l) => {
          const [ticker, qty] = l.split(/[:\s,]+/);
          return { ticker: ticker.toUpperCase(), qty: Number(qty) || 0 };
        })
        .filter((p) => p.ticker && p.qty !== 0);
      if (!positions.length) throw new Error("Enter at least one TICKER:qty line.");
      const env = await api.analyzePortfolio(positions);
      setResult({ data: env.data, demo: !!env.meta.demo, as_of: env.meta.as_of });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Analysis failed.");
    } finally {
      setLoading(false);
    }
  }

  const d = result?.data;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Portfolio</h1>
          <p className="mt-1 text-sm text-slate-500">
            Risk, stress scenarios and factor exposure on real prices
          </p>
        </div>
        {result?.demo && <DemoBadge asOf={result.as_of} />}
      </div>

      <Card title="Positions" subtitle="One TICKER:qty per line">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={4}
          className="w-full rounded-lg border border-ink-700 bg-ink-900 p-3 font-mono text-sm text-white"
          spellCheck={false}
        />
        <button
          onClick={analyze}
          disabled={loading}
          className="mt-3 rounded-lg bg-bronze-600 px-5 py-2 text-sm font-semibold text-white hover:bg-bronze-500 disabled:opacity-50"
        >
          {loading ? "Analyzing…" : "Analyze portfolio"}
        </button>
        {error && <p className="mt-2 text-sm text-rose-400">{error}</p>}
      </Card>

      {d && (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <Metric label="Beta (vs SPY)" value={d.beta?.toFixed(2) ?? "—"} />
            <Metric label="VaR 95% (daily)" value={d.var_95 != null ? pct(d.var_95) : "—"} />
            <Metric label="CVaR 95% (daily)" value={d.cvar_95 != null ? pct(d.cvar_95) : "—"} />
            <Metric label="Max drawdown" value={d.max_drawdown != null ? pct(d.max_drawdown) : "—"} />
            <Metric label="Sharpe" value={d.sharpe?.toFixed(2) ?? "—"} />
            <Metric label="Sortino" value={d.sortino?.toFixed(2) ?? "—"} />
            <Metric label="Diversification" value={d.diversification_score != null ? `${Math.round(d.diversification_score)}/100` : "—"} />
            <Metric label="Sector HHI" value={d.sector_hhi?.toFixed(2) ?? "—"} />
          </div>

          {d.stress_tests && d.stress_tests.length > 0 && (
            <Card
              title="Stress scenarios"
              subtitle={`Historical window replay · as-of ${d.as_of ?? "—"} · status ${d.data_status}`}
            >
              <Table head={["Scenario", "Return", "Max DD", "Worst holding"]}>
                {d.stress_tests.map((s) => (
                  <tr key={s.scenario} className="hover:bg-ink-800/50">
                    <Td className="text-slate-200">{s.scenario}</Td>
                    <Td className={`font-mono ${s.portfolio_return == null ? "text-slate-500" : s.portfolio_return >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                      {s.portfolio_return == null ? `n/a (${s.reason ?? "no data"})` : ret1(s.portfolio_return)}
                    </Td>
                    <Td className="font-mono text-slate-300">
                      {s.max_drawdown == null ? "—" : pct(s.max_drawdown)}
                    </Td>
                    <Td className="font-mono text-slate-400">{s.worst_holding ?? "—"}</Td>
                  </tr>
                ))}
              </Table>
            </Card>
          )}

          {d.factor_exposure && (
            <Card title="Factor exposure" subtitle="Engine-B style exposures">
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                {Object.entries(d.factor_exposure).map(([k, v]) => (
                  <Metric key={k} label={k} value={typeof v === "number" ? ratio1(v) : "—"} />
                ))}
              </div>
            </Card>
          )}
          <p className="text-xs italic text-slate-500">{d.disclaimer || DISCLAIMER}</p>
        </>
      )}
    </div>
  );
}
