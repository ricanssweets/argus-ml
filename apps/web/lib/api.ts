/**
 * Typed API client for ARGUS (/api/v1). Per API_SPEC.md every response is an
 * envelope: { data, meta: { as_of, data_status, request_id } }.
 *
 * When the API is unreachable (or returns an error), every call falls back to
 * clearly-marked SYNTHETIC demo data and sets meta.demo = true so the UI can
 * render the "DEMO DATA" badge.
 */
import type {
  AlertItem,
  AlertRule,
  Asset,
  BacktestResult,
  CalibrationBucket,
  DegradationFlag,
  Envelope,
  MarketRegime,
  Meta,
  ModelVersionRow,
  PortfolioAnalysis,
  PortfolioPosition,
  Prediction,
  PriceBar,
  RegimeHistoryRow,
  ResearchAnswer,
  ScannerRow,
} from "./types";
import {
  DEFAULT_BACKTEST_CONFIG,
  DEMO_AS_OF,
  demoAlerts,
  demoAlertRules,
  demoAsset,
  demoAssetList,
  demoBacktest,
  demoCalibration,
  demoDegradationFlags,
  demoLockedToday,
  demoModels,
  demoPortfolioAnalysis,
  demoPrediction,
  demoPrices,
  demoRegime,
  demoRegimeHistory,
  demoResearch,
  demoScanner,
  HORIZONS,
} from "./demo-data";
import type { BacktestConfig } from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_ARGUS_API_URL ?? "http://localhost:8000";
const TIMEOUT_MS = 2500;

function demoMeta(data_status: Meta["data_status"] = "MISSING"): Meta {
  return {
    as_of: DEMO_AS_OF,
    data_status,
    request_id: "demo-synthetic",
    demo: true,
  };
}

async function fetchEnvelope<T>(
  path: string,
  demo: () => T,
  init?: RequestInit
): Promise<Envelope<T>> {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
    const res = await fetch(`${API_BASE}/api/v1${path}`, {
      ...init,
      signal: ctrl.signal,
      cache: "no-store",
    });
    clearTimeout(timer);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = (await res.json()) as Envelope<T>;
    if (!json || !("data" in json) || !("meta" in json) || json.data == null)
      throw new Error("bad envelope");
    return { ...json, meta: { ...json.meta, demo: false } };
  } catch {
    return { data: demo(), meta: demoMeta() };
  }
}

export const api = {
  getAsset: (ticker: string): Promise<Envelope<Asset>> =>
    fetchEnvelope(`/assets/${ticker.toUpperCase()}`, () => demoAsset(ticker)),

  getPrices: (ticker: string, range = "1y"): Promise<Envelope<PriceBar[]>> =>
    fetchEnvelope(
      `/assets/${ticker.toUpperCase()}/prices?range=${range}&interval=1d`,
      () => demoPrices(ticker)
    ),

  getPredictions: (
    ticker: string,
    horizons: number[] = HORIZONS
  ): Promise<Envelope<Prediction[]>> =>
    fetchEnvelope(
      `/predictions/${ticker.toUpperCase()}?horizons=${horizons.join(",")}`,
      () => horizons.map((h) => demoPrediction(ticker, h))
    ),

  getPrediction: async (
    ticker: string,
    horizon: number
  ): Promise<Envelope<Prediction>> => {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
      const res = await fetch(
        `${API_BASE}/api/v1/predictions/${ticker.toUpperCase()}?horizons=${horizon}`,
        { signal: ctrl.signal, cache: "no-store" }
      );
      clearTimeout(timer);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = (await res.json()) as Envelope<Prediction[]>;
      const first = json.data[0];
      if (!first) throw new Error("empty prediction list");
      return {
        data: first,
        meta: { ...json.meta, demo: false },
      };
    } catch {
      return { data: demoPrediction(ticker, horizon), meta: demoMeta() };
    }
  },

  getScanner: (limit = 25): Promise<Envelope<ScannerRow[]>> =>
    fetchEnvelope(
      `/scanner?setup=momentum_breakout&horizon=20&limit=${limit}`,
      () => demoScanner(limit)
    ).then((env) => ({
      ...env,
      // Normalize: real API uses composite_score/setup_tags and omits name.
      data: ((env.data as any[]) ?? []).map((s: any) => ({
        ticker: s.ticker,
        name: s.name ?? s.ticker,
        sector: s.sector ?? "",
        score: s.score ?? s.composite_score ?? 0,
        p_positive: s.p_positive ?? 0.5,
        expected_return: s.expected_return ?? 0,
        risk_reward: s.risk_reward ?? 0,
        setup: s.setup ?? s.setup_tags?.[0] ?? "—",
        horizon: s.horizon ?? 20,
      })),
    })),

  getAssets: (): Promise<Envelope<Asset[]>> =>
    fetchEnvelope(`/assets?limit=50`, () => demoAssetList()),

  getRegime: (): Promise<Envelope<MarketRegime>> =>
    fetchEnvelope(`/market-regime`, () => demoRegime()),

  getModels: (): Promise<Envelope<ModelVersionRow[]>> =>
    fetchEnvelope(`/models`, () => demoModels()),

  getCalibration: (
    _version: string,
    _horizon: number
  ): Promise<Envelope<CalibrationBucket[]>> =>
    fetchEnvelope(
      `/models/${_version}/calibration?horizon=${_horizon}`,
      () => demoCalibration()
    ),

  getDegradationFlags: (): Promise<Envelope<DegradationFlag[]>> =>
    fetchEnvelope(`/models/ARGUS-EQ-1.0/metrics`, () =>
      demoDegradationFlags()
    ),

  getLockedToday: (): Promise<Envelope<Prediction[]>> =>
    fetchEnvelope(`/predictions?horizon=20&min_score=70`, () =>
      demoLockedToday()
    ),

  getRegimeHistory: (
    days = 365
  ): Promise<Envelope<{ history: RegimeHistoryRow[]; count: number }>> =>
    fetchEnvelope(`/market-regime/history?days=${days}`, () => ({
      history: demoRegimeHistory(days),
      count: days,
    })),

  analyzePortfolio: (
    positions: PortfolioPosition[]
  ): Promise<Envelope<PortfolioAnalysis>> =>
    fetchEnvelope(
      `/portfolio/analyze`,
      () => demoPortfolioAnalysis(positions),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ positions }),
      }
    ),

  getAlerts: (): Promise<Envelope<AlertItem[]>> =>
    fetchEnvelope(`/alerts`, () => demoAlerts()),

  getAlertRules: (): Promise<Envelope<AlertRule[]>> =>
    fetchEnvelope(`/alert-rules`, () => demoAlertRules()),

  createAlertRule: (
    rule_type: string,
    ticker: string,
    params: Record<string, number | string>
  ): Promise<Envelope<AlertRule>> =>
    fetchEnvelope(
      `/alert-rules`,
      () => demoAlertRules()[0],
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rule_type, ticker, params }),
      }
    ),

  askResearch: (question: string): Promise<Envelope<ResearchAnswer>> =>
    fetchEnvelope(
      `/research`,
      () => demoResearch(question),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      }
    ),

  /** Backtests are heavy compute — always async job based. Demo runs locally. */
  runBacktest: async (config: BacktestConfig): Promise<BacktestResult> => {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
      const res = await fetch(`${API_BASE}/api/v1/backtests`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
        signal: ctrl.signal,
      });
      clearTimeout(timer);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const job = (await res.json()) as { backtest_id?: string; id?: string };
      const id = job.backtest_id ?? job.id;
      if (!id) throw new Error("no backtest id");
      const detail = await fetch(`${API_BASE}/api/v1/backtests/${id}`, {
        cache: "no-store",
      });
      if (!detail.ok) throw new Error(`HTTP ${detail.status}`);
      const env = (await detail.json()) as Envelope<BacktestResult>;
      return env.data;
    } catch {
      return demoBacktest(config);
    }
  },
};

export { DEFAULT_BACKTEST_CONFIG, DEMO_AS_OF };
export type { BacktestConfig };
