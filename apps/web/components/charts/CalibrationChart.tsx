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
  // Defensive: the live API may omit `observed_rate`/`mean_predicted` (or send
  // a different bucket shape). Rows without both numbers are dropped so the
  // chart can never receive NaN.
  const data = buckets
    .filter(
      (b) =>
        typeof b.predicted === "number" &&
        Number.isFinite(b.predicted) &&
        typeof b.observed === "number" &&
        Number.isFinite(b.observed)
    )
    .map((b) => ({
      predicted: Math.round((b.predicted as number) * 100),
      observed: Math.round((b.observed as number) * 100),
      diagonal: Math.round((b.predicted as number) * 100),
      n: typeof b.n === "number" ? b.n : null,
    }));

  if (data.length === 0) {
    return (
      <div className="flex h-80 w-full items-center justify-center rounded-lg border border-dashed border-ink-700 text-sm text-slate-500">
        No calibration data available — the API returned no usable buckets.
      </div>
    );
  }

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
