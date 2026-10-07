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
  currentPrice: number;
}) {
  const ma50 = movingAverage(bars, 50);
  const ma200 = movingAverage(bars, 200);
  const hist = bars.map((b, i) => ({
    label: b.time.slice(5),
    close: b.close,
    ma50: ma50[i],
    ma200: ma200[i],
    expected: null,
    high: null,
    low: null,
  }));
  const fwd = band.map((f) => ({
    label: f.label,
    close: null,
    ma50: null,
    ma200: null,
    expected: f.expected === null ? null : currentPrice * (1 + f.expected),
    high: f.high === null ? null : currentPrice * (1 + f.high),
    low: f.low === null ? null : currentPrice * (1 + f.low),
  }));
  const data = [...hist.slice(-180), ...fwd];

  return (
    <div className="h-80 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="#1a2230" strokeDasharray="3 3" />
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
              backgroundColor: "#141b26",
              border: "1px solid #243041",
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
            fill="#0ea5e9"
            fillOpacity={0.12}
            connectNulls
            name="Prediction band (high)"
            legendType="none"
          />
          <Area
            type="monotone"
            dataKey="low"
            stroke="none"
            fill="#0ea5e9"
            fillOpacity={0}
            connectNulls
            name="Prediction band (low)"
            legendType="none"
          />
          <Line
            type="monotone"
            dataKey="close"
            stroke="#e2e8f0"
            strokeWidth={1.8}
            dot={false}
            connectNulls
            name="Close"
          />
          <Line
            type="monotone"
            dataKey="ma50"
            stroke="#f59e0b"
            strokeWidth={1.2}
            dot={false}
            connectNulls
            name="MA 50"
          />
          <Line
            type="monotone"
            dataKey="ma200"
            stroke="#8b5cf6"
            strokeWidth={1.2}
            dot={false}
            connectNulls
            name="MA 200"
          />
          <Line
            type="monotone"
            dataKey="expected"
            stroke="#0ea5e9"
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
