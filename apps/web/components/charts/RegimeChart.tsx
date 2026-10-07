"use client";

import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const REGIME_COLORS: Record<string, string> = {
  Bull: "#10b981",
  Neutral: "#64748b",
  Bear: "#f43f5e",
  HighVol: "#f59e0b",
};

export function RegimeChart({
  history,
}: {
  history: { time: string; regime: string; confidence: number }[];
}) {
  const data = history.map((h) => ({
    label: h.time.slice(5),
    confidence: Math.round(h.confidence * 100),
    regime: h.regime,
  }));
  return (
    <div className="h-80 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={data}
          margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
        >
          <CartesianGrid stroke="#1a2230" strokeDasharray="3 3" />
          <XAxis
            dataKey="label"
            tick={{ fill: "#64748b", fontSize: 11 }}
            minTickGap={48}
          />
          <YAxis
            domain={[0, 100]}
            tick={{ fill: "#64748b", fontSize: 11 }}
            tickFormatter={(v: number) => `${v}%`}
            width={52}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "#141b26",
              border: "1px solid #243041",
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(
              v: number,
              name: string,
              props: { payload?: { regime?: string } }
            ) =>
              name === "confidence"
                ? [`${v}% · ${props.payload?.regime}`, "Confidence"]
                : [v, name]
            }
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar
            dataKey="confidence"
            name="Regime confidence"
            radius={[2, 2, 0, 0]}
          >
            {data.map((d, i) => (
              <Cell
                key={i}
                fill={REGIME_COLORS[d.regime] ?? "#64748b"}
                fillOpacity={0.75}
              />
            ))}
          </Bar>
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
