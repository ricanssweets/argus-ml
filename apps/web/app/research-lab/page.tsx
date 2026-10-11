"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { DemoBadge } from "@/components/DemoBadge";
import { DISCLAIMER } from "@/lib/format";
import type { ResearchAnswer } from "@/lib/types";

const EXAMPLES = [
  "Which stocks have the highest probability of outperforming over the next 20 trading days?",
  "Show me stocks with probability above 70% of positive returns and risk/reward above 3:1.",
  "Why did NVDA's prediction score fall today?",
  "How accurate has this model been during previous bear markets?",
];

export default function ResearchLabPage() {
  const [q, setQ] = useState("");
  const [answer, setAnswer] = useState<ResearchAnswer | null>(null);
  const [demo, setDemo] = useState(false);
  const [asOf, setAsOf] = useState("");
  const [loading, setLoading] = useState(false);

  async function ask(question: string) {
    const qq = question.trim();
    if (!qq || loading) return;
    setLoading(true);
    setQ(qq);
    try {
      const env = await api.askResearch(qq);
      setAnswer(env.data);
      setDemo(!!env.meta.demo);
      setAsOf(env.meta.as_of);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl tracking-[0.08em] text-stone-100">Research Lab</h1>
          <p className="mt-1 text-sm text-slate-500">
            Answers only from platform data — every number cites a stored record
          </p>
        </div>
        {demo && <DemoBadge asOf={asOf} />}
      </div>

      <Card title="Ask" subtitle="Deterministic query planner over predictions, backtests and observatory tables">
        <div className="flex gap-3">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask(q)}
            placeholder="Which ETFs have the strongest momentum with acceptable valuation?"
            className="flex-1 rounded-lg border border-ink-700 bg-ink-900 px-4 py-2.5 text-sm text-white"
          />
          <button
            onClick={() => ask(q)}
            disabled={loading}
            className="rounded-lg bg-bronze-600 px-5 py-2 text-sm font-semibold text-white hover:bg-bronze-500 disabled:opacity-50"
          >
            {loading ? "…" : "Ask"}
          </button>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => ask(ex)}
              className="rounded-full border border-ink-700 px-3 py-1 text-xs text-slate-400 hover:border-bronze-600 hover:text-slate-200"
            >
              {ex.length > 64 ? ex.slice(0, 64) + "…" : ex}
            </button>
          ))}
        </div>
      </Card>

      {answer && (
        <Card title="Answer" subtitle={`Status ${answer.data_status}`}>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-200">
            {answer.answer}
          </p>
          {answer.citations.length > 0 && (
            <div className="mt-4 border-t border-ink-800 pt-3">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Citations
              </p>
              <div className="flex flex-wrap gap-2">
                {answer.citations.map((c, i) => (
                  <Badge key={i} tone="neutral">
                    {c.table} · {c.id} · {c.as_of}
                  </Badge>
                ))}
              </div>
            </div>
          )}
          <p className="mt-4 text-xs italic text-slate-500">
            {answer.disclaimer || DISCLAIMER}
          </p>
        </Card>
      )}
    </div>
  );
}
