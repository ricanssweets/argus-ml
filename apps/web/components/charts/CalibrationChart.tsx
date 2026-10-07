"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { CalibrationBucket } from "@/lib/types";

export function CalibrationChart({ buckets }: { buckets: CalibrationBucket[] }) {
  const data = buckets.map((b) => ({
    predicted: Math.round(b.predicted * 100),
    observed: Math.round(b.observed * 100),
    diagonal: Math.round(b.predicted * 100),
    n: b.n,
  }));
  return (
    <div className="h-80 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="#1a2230" strokeDasharray="3 3" />
          <XAxis
            dataKey="predicted"
            tick={{ fill: "#64748b", fontSize: 11 }}
            label={{
              value: "Predicted P (%)",
              position: "insideBottomRight",
              offset: -2,
              fill: "#64748b",
              fontSize: 11,
            }}
          />
          <YAxis
            tick={{ fill: "#64748b", fontSize: 11 }}
            label={{
              value: "Observed freq (%)",
              angle: -90,
              position: "insideLeft",
              fill: "#64748b",
              fontSize: 11,
            }}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "#141b26",
              border: "1px solid #243041",
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(v: number, name: string, props: { payload?: { n?: number } }) =>
              name === "n"
                ? [v, "Samples"]
                : [`${v}%`, name === "observed" ? "Observed" : "Perfect calibration"]
            }
            labelFormatter={(l) => `Predicted ${l}%`}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <ReferenceLine y={0} stroke="#243041" />
          <Line
            type="monotone"
            dataKey="diagonal"
            stroke="#475569"
            strokeDasharray="6 4"
            dot={false}
            name="Perfect calibration"
          />
          <Line
            type="monotone"
            dataKey="observed"
            stroke="#0ea5e9"
            strokeWidth={2.2}
            dot={{ r: 3, fill: "#0ea5e9" }}
            name="ARGUS-EQ-1.0 (20d)"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
