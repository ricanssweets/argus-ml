"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export function EquityChart({
  equity,
}: {
  equity: { time: string; strategy: number; benchmark: number }[];
}) {
  const data = equity.map((e) => ({
    label: e.time.slice(0, 7),
    Strategy: Math.round(e.strategy * 1000) / 1000,
    SPY: Math.round(e.benchmark * 1000) / 1000,
  }));
  return (
    <div className="h-80 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="#1a2230" strokeDasharray="3 3" />
          <XAxis
            dataKey="label"
            tick={{ fill: "#64748b", fontSize: 11 }}
            minTickGap={40}
          />
          <YAxis
            tick={{ fill: "#64748b", fontSize: 11 }}
            tickFormatter={(v: number) => `${v.toFixed(1)}x`}
            width={52}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "#141b26",
              border: "1px solid #243041",
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(v: number, name: string) => [`${v.toFixed(2)}x`, name]}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line
            type="monotone"
            dataKey="Strategy"
            stroke="#0ea5e9"
            strokeWidth={2}
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="SPY"
            stroke="#64748b"
            strokeWidth={1.5}
            strokeDasharray="6 4"
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
