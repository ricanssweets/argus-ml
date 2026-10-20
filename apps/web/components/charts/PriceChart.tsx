"use client";

import {
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  Area,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { PriceBar } from "@/lib/types";
import { movingAverage } from "@/lib/demo-data";

export interface ForwardBand {
  label: string;
  expected: number | null;
  high: number | null;
  low: number | null;
}

export function PriceChart({
  bars,
  band,
  currentPrice,
}: {
  bars: PriceBar[];
  band: ForwardBand[];
  /** Nullable: the live API may omit Asset.price. */
  currentPrice: number | null;
}) {
  const safeBars = Array.isArray(bars) ? bars : [];
  const ma50 = movingAverage(safeBars, 50);
  const ma200 = movingAverage(safeBars, 200);
  const hist = safeBars.map((b, i) => ({
    label: (b.time ?? "").slice(5),
    close: b.close,
    ma50: ma50[i],
    ma200: ma200[i],
    expected: null,
    high: null,
    low: null,
  }));
  const base = typeof currentPrice === "number" && Number.isFinite(currentPrice)
    ? currentPrice
    : null;
  const fwd = (Array.isArray(band) ? band : []).map((f) => ({
    label: f.label,
    close: null,
    ma50: null,
    ma200: null,
    expected:
      base !== null && f.expected !== null ? base * (1 + f.expected) : null,
    high: base !== null && f.high !== null ? base * (1 + f.high) : null,
    low: base !== null && f.low !== null ? base * (1 + f.low) : null,
  }));
  const data = [...hist.slice(-180), ...fwd];

  if (data.length === 0) {
    return (
      <div className="flex h-80 w-full items-center justify-center rounded-lg border border-dashed border-ink-700 text-sm text-slate-500">
        No price series available — the API returned no usable bars.
      </div>
    );
  }

  return (
    <div className="h-80 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="#232328" strokeDasharray="3 3" />
          <XAxis
            dataKey="label"
            tick={{ fill: "#64748b", fontSize: 11 }}
            minTickGap={48}
          />
          <YAxis
            domain={["auto", "auto"]}
            tick={{ fill: "#64748b", fontSize: 11 }}
            tickFormatter={(v: number) => `$${Math.round(v)}`}
            width={64}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "#1a1a1e",
              border: "1px solid #313138",
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(v, name) =>
              typeof v === "number" ? [`$${v.toFixed(2)}`, name] : ["—", name]
            }
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Area
            type="monotone"
            dataKey="high"
            stroke="none"
            fill="#c99763"
            fillOpacity={0.12}
            connectNulls
            name="Prediction band (high)"
            legendType="none"
          />
          <Area
            type="monotone"
            dataKey="low"
            stroke="none"
            fill="#c99763"
            fillOpacity={0}
            connectNulls
            name="Prediction band (low)"
            legendType="none"
          />
          <Line
            type="monotone"
            dataKey="close"
            stroke="#e9e2d5"
            strokeWidth={1.8}
            dot={false}
            connectNulls
            name="Close"
          />
          <Line
            type="monotone"
            dataKey="ma50"
            stroke="#a56e40"
            strokeWidth={1.2}
            dot={false}
            connectNulls
            name="MA 50"
          />
          <Line
            type="monotone"
            dataKey="ma200"
            stroke="#175049"
            strokeWidth={1.2}
            dot={false}
            connectNulls
            name="MA 200"
          />
          <Line
            type="monotone"
            dataKey="expected"
            stroke="#c99763"
            strokeWidth={1.8}
            strokeDasharray="6 4"
            dot={false}
            connectNulls
            name="Expected path (20d)"
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
