"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, Td } from "@/components/ui/table";
import { DemoBadge } from "@/components/DemoBadge";
import { fmtDateTime } from "@/lib/format";
import type { AlertItem, AlertRule } from "@/lib/types";

const RULE_TYPES = [
  "score_change",
  "prob_change",
  "breakout",
  "earnings",
  "unusual_volume",
  "unusual_options",
  "vol_spike",
  "regime_change",
  "news",
];

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [demo, setDemo] = useState(false);
  const [asOf, setAsOf] = useState("");
  const [form, setForm] = useState({ rule_type: "breakout", ticker: "NVDA", params: "{}" });
  const [msg, setMsg] = useState<string | null>(null);

  async function load() {
    const [a, r] = await Promise.all([api.getAlerts(), api.getAlertRules()]);
    setAlerts(a.data);
    setRules(r.data);
    setDemo(!!a.meta.demo || !!r.meta.demo);
    setAsOf(a.meta.as_of);
  }

  useEffect(() => {
    load();
  }, []);

  async function create() {
    setMsg(null);
    try {
      const params = JSON.parse(form.params || "{}");
      await api.createAlertRule(form.rule_type, form.ticker.toUpperCase(), params);
      setMsg("Rule created.");
      load();
    } catch (e) {
      setMsg(e instanceof Error ? `Failed: ${e.message}` : "Failed.");
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Alerts</h1>
          <p className="mt-1 text-sm text-slate-500">
            In-app delivery is live · push/email are explicit stubs until configured
          </p>
        </div>
        {demo && <DemoBadge asOf={asOf} />}
      </div>

      <Card title="New alert rule" subtitle="Evaluated by the Phase-7 rule engine">
        <div className="flex flex-wrap gap-3">
          <select
            value={form.rule_type}
            onChange={(e) => setForm({ ...form, rule_type: e.target.value })}
            className="rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-white"
          >
            {RULE_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <input
            value={form.ticker}
            onChange={(e) => setForm({ ...form, ticker: e.target.value })}
            placeholder="TICKER"
            className="w-28 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 font-mono text-sm text-white"
          />
          <input
            value={form.params}
            onChange={(e) => setForm({ ...form, params: e.target.value })}
            placeholder='params JSON, e.g. {"min_delta":10}'
            className="min-w-56 flex-1 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 font-mono text-sm text-white"
          />
          <button
            onClick={create}
            className="rounded-lg bg-sky-600 px-5 py-2 text-sm font-semibold text-white hover:bg-sky-500"
          >
            Create rule
          </button>
        </div>
        {msg && <p className="mt-2 text-sm text-slate-400">{msg}</p>}
      </Card>

      <Card title={`Alert rules (${rules.length})`} subtitle="Point-in-time evaluation, no lookahead">
        <Table head={["ID", "Type", "Ticker", "Params", "Active"]}>
          {rules.map((r) => (
            <tr key={r.id} className="hover:bg-ink-800/50">
              <Td className="font-mono text-xs text-slate-400">{r.id}</Td>
              <Td><Badge tone="neutral">{r.rule_type}</Badge></Td>
              <Td className="font-mono text-slate-200">{r.ticker ?? "—"}</Td>
              <Td className="font-mono text-xs text-slate-500">{JSON.stringify(r.params)}</Td>
              <Td>{r.is_active ? "yes" : "no"}</Td>
            </tr>
          ))}
        </Table>
      </Card>

      <Card title={`Alerts (${alerts.length})`} subtitle="In-app channel">
        <Table head={["When", "Type", "Ticker", "Title", "Severity"]}>
          {alerts.map((a) => (
            <tr key={a.id} className="hover:bg-ink-800/50">
              <Td className="font-mono text-xs text-slate-400">{fmtDateTime(a.created_at)}</Td>
              <Td><Badge tone="neutral">{a.alert_type}</Badge></Td>
              <Td className="font-mono text-slate-200">{a.ticker ?? "—"}</Td>
              <Td className="text-slate-300">{a.title}</Td>
              <Td className="text-slate-400">{a.severity}</Td>
            </tr>
          ))}
        </Table>
        {alerts.length === 0 && (
          <p className="py-4 text-center text-sm text-slate-500">
            No alerts yet — create a rule above, then run scripts/eval_alerts.py (cron) or POST /api/v1/alerts/evaluate.
          </p>
        )}
      </Card>
    </div>
  );
}
