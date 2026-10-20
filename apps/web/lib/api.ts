/**
 * Typed API client for ARGUS (/api/v1). Per API_SPEC.md every response is an
 * envelope: { data, meta: { as_of, data_status, request_id } }.
 *
 * HONESTY CONTRACT (this is the whole point of the file):
 * - A failed call still falls back to SYNTHETIC demo data (so the UI renders),
 *   but it ALWAYS records WHY in `meta.error = { kind, status, message }` and
 *   sets `meta.demo = true`. `kind` distinguishes config / network / timeout /
 *   http / bad_envelope / empty, so a 401/500/timeout can never again look
 *   exactly like a successful live load.
 * - Every payload is run through a normaliser that only reads fields it can
 *   prove are finite strings/numbers. A live API that omits brier, n, a
 *   calibration bucket or a whole `bars` wrapper can no longer make a page
 *   throw a TypeError; the missing metric becomes null and the UI renders "—".
 * - In production with NEXT_PUBLIC_ARGUS_API_URL unset the client refuses to
 *   silently target localhost and reports kind === "config" (see lib/config.ts).
 */
import type {
  AlertItem,
  AlertRule,
  Asset,
  BacktestConfig,
  BacktestMetrics,
  BacktestResult,
  CalibrationBucket,
  DataError,
  DataErrorKind,
  DataStatus,
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
import { API_BASE, API_CONFIG_ERROR, TIMEOUT_MS } from "./config";
import { DISCLAIMER } from "./format";

/* ------------------------------------------------------------------ errors */

class ApiProblem extends Error {
  kind: DataErrorKind;
  status?: number;
  constructor(kind: DataErrorKind, message: string, status?: number) {
    super(message);
    this.name = "ApiProblem";
    this.kind = kind;
    this.status = status;
  }
}

function dataError(kind: DataErrorKind, message: string, status?: number): DataError {
  return status === undefined ? { kind, message } : { kind, status, message };
}

function classifyError(e: unknown): DataError {
  if (e instanceof ApiProblem) return dataError(e.kind, e.message, e.status);
  if (e instanceof Error) {
    if (e.name === "AbortError" || e.name === "TimeoutError")
      return dataError("timeout", `no response within ${TIMEOUT_MS} ms`);
    if (e instanceof SyntaxError)
      return dataError("bad_envelope", "response body was not valid JSON");
    if (e instanceof TypeError)
      return dataError("network", e.message || "network request failed");
    return dataError("unknown", e.message);
  }
  return dataError("unknown", "unexpected error");
}

/* ---------------------------------------------------------------- coercion */

function numOrNull(v: unknown): number | null {
  const n =
    typeof v === "number"
      ? v
      : typeof v === "string" && v.trim() !== ""
        ? Number(v)
        : NaN;
  return Number.isFinite(n) ? n : null;
}

function strOrNull(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

function strOr(v: unknown, fallback: string): string {
  return strOrNull(v) ?? fallback;
}

function arrOf(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function strArr(v: unknown): string[] {
  return arrOf(v).filter((s): s is string => typeof s === "string");
}

/** Plain-object view of untrusted JSON. Never assume a shape. */
function rec(v: unknown): Record<string, unknown> {
  return v !== null && typeof v === "object" ? (v as Record<string, unknown>) : {};
}

/* -------------------------------------------------------------- normalisers */

function normalizeAsset(raw: unknown): Asset {
  const a = rec(raw);
  const price = numOrNull(a.price);
  const prev = numOrNull(a.prev_close);
  const mktCap = numOrNull(a.market_cap) ?? numOrNull(a.mkt_cap);
  return {
    ticker: strOr(a.ticker, ""),
    name: strOrNull(a.name),
    asset_type: strOr(a.asset_type, "STOCK") as Asset["asset_type"],
    exchange: strOrNull(a.exchange),
    currency: strOrNull(a.currency) ?? "USD",
    sector: strOrNull(a.sector),
    industry: strOrNull(a.industry),
    price,
    prev_close: prev,
    change:
      numOrNull(a.change) ??
      (price !== null && prev !== null ? price - prev : null),
    change_pct: numOrNull(a.change_pct),
    mkt_cap: mktCap,
    market_cap: mktCap,
    data_status: strOr(a.data_status, "UNCONFIRMED") as DataStatus,
    ...(strOrNull(a.disclaimer) ? { disclaimer: a.disclaimer as string } : {}),
  };
}

function normalizeAssets(raw: unknown): Asset[] {
  const list = Array.isArray(raw) ? raw : arrOf(rec(raw).assets);
  return list.map(normalizeAsset).filter((a) => a.ticker !== "");
}

/**
 * The live API wraps bars in { ticker, interval, bars: [...] }; older / demo
 * shapes are a bare array. Both are accepted; unusable bars are dropped so the
 * chart can never receive NaN.
 */
function normalizeBars(raw: unknown): PriceBar[] {
  const list = Array.isArray(raw) ? raw : arrOf(rec(raw).bars);
  return list
    .map((b): PriceBar => {
      const r = rec(b);
      return {
        time: strOr(r.time ?? r.date, ""),
        open: numOrNull(r.open) ?? 0,
        high: numOrNull(r.high) ?? 0,
        low: numOrNull(r.low) ?? 0,
        close: numOrNull(r.close) ?? 0,
        volume: numOrNull(r.volume) ?? 0,
      };
    })
    .filter((b) => b.time !== "" && b.close > 0);
}

function normalizePrediction(raw: unknown): Prediction {
  const p = rec(raw);
  const explanation = rec(p.explanation);
  return {
    ticker: strOr(p.ticker, ""),
    horizon: numOrNull(p.horizon) ?? 0,
    as_of: strOr(p.as_of, ""),
    model_version: strOr(p.model_version, "—"),
    regime: strOr(p.regime, "—"),
    p_positive: numOrNull(p.p_positive) ?? 0,
    p_negative: numOrNull(p.p_negative) ?? 0,
    expected_return: numOrNull(p.expected_return),
    expected_vol: numOrNull(p.expected_vol),
    expected_high: numOrNull(p.expected_high),
    expected_low: numOrNull(p.expected_low),
    ci: arrOf(p.ci).map(numOrNull).filter((x): x is number => x !== null),
    downside_risk: numOrNull(p.downside_risk),
    upside_potential: numOrNull(p.upside_potential),
    risk_reward: numOrNull(p.risk_reward),
    composite_score: numOrNull(p.composite_score) ?? 0,
    trend_strength: numOrNull(p.trend_strength),
    momentum_score: numOrNull(p.momentum_score),
    fundamental_score: numOrNull(p.fundamental_score),
    risk_score: numOrNull(p.risk_score),
    volatility_score: numOrNull(p.volatility_score),
    confidence: numOrNull(p.confidence) ?? 0,
    data_status: strOr(p.data_status, "UNCONFIRMED") as DataStatus,
    explanation: {
      reasons: strArr(explanation.reasons),
      risks: strArr(explanation.risks),
      invalidated_if: strArr(explanation.invalidated_if),
    },
    ...(p.engine_signals && typeof p.engine_signals === "object"
      ? { engine_signals: p.engine_signals as Prediction["engine_signals"] }
      : {}),
    ...(strOrNull(p.pipeline) ? { pipeline: p.pipeline as string } : {}),
    ...(typeof p.calibrated === "boolean" ? { calibrated: p.calibrated } : {}),
    disclaimer: strOr(p.disclaimer, DISCLAIMER),
  };
}

function normalizePredictions(raw: unknown): Prediction[] {
  const list = Array.isArray(raw) ? raw : arrOf(rec(raw).predictions);
  return list.map(normalizePrediction);
}

function normalizeScanner(raw: unknown): ScannerRow[] {
  const list = Array.isArray(raw) ? raw : arrOf(rec(raw).rows);
  return list
    .map((s): ScannerRow => {
      const r = rec(s);
      const tags = strArr(r.setup_tags);
      return {
        ticker: strOr(r.ticker, ""),
        name: strOr(r.name ?? r.ticker, ""),
        sector: strOr(r.sector, ""),
        score: numOrNull(r.score ?? r.composite_score) ?? 0,
        p_positive: numOrNull(r.p_positive) ?? 0.5,
        expected_return: numOrNull(r.expected_return) ?? 0,
        risk_reward: numOrNull(r.risk_reward) ?? 0,
        setup: strOr(r.setup ?? tags[0], "—"),
        horizon: numOrNull(r.horizon) ?? 20,
      };
    })
    .filter((s) => s.ticker !== "");
}

function normalizeModels(raw: unknown): ModelVersionRow[] {
  const list = Array.isArray(raw) ? raw : arrOf(rec(raw).models);
  return list.map((m): ModelVersionRow => {
    const r = rec(m);
    const row: ModelVersionRow = {
      version: strOr(r.version, "—"),
      status: strOr(r.status, "EXPERIMENT") as ModelVersionRow["status"],
      horizons: arrOf(r.horizons)
        .map(numOrNull)
        .filter((x): x is number => x !== null),
      brier: numOrNull(r.brier),
      roc_auc: numOrNull(r.roc_auc),
      cal_error: numOrNull(r.cal_error ?? r.calibration_error),
      n: numOrNull(r.n),
    };
    const family = strOrNull(r.family);
    if (family) row.family = family;
    if (typeof r.weights_are_priors === "boolean")
      row.weights_are_priors = r.weights_are_priors;
    const dataStatus = strOrNull(r.data_status);
    if (dataStatus) row.data_status = dataStatus as DataStatus;
    return row;
  });
}

/**
 * Accepts a bare bucket array, a { buckets: [...] } CalibrationCurve, or the
 * older { predicted, observed } naming. Missing numbers stay null (never NaN).
 */
function normalizeCalibration(raw: unknown): CalibrationBucket[] {
  const list = Array.isArray(raw) ? raw : arrOf(rec(raw).buckets);
  return list.map((b): CalibrationBucket => {
    const r = rec(b);
    const lo = Array.isArray(r.bin) ? numOrNull(r.bin[0]) : null;
    const hi = Array.isArray(r.bin) ? numOrNull(r.bin[1]) : null;
    const bin: [number, number] | null =
      lo !== null && hi !== null ? [lo, hi] : null;
    return {
      predicted:
        numOrNull(r.predicted) ??
        numOrNull(r.mean_predicted) ??
        (bin ? (bin[0] + bin[1]) / 2 : null),
      observed: numOrNull(r.observed) ?? numOrNull(r.observed_rate),
      n: numOrNull(r.n),
      bin,
    };
  });
}

/**
 * /models/{version}/metrics returns ModelMetrics with a nested
 * `degradation_flags` array; older shapes return a bare array.
 */
function normalizeFlags(raw: unknown): DegradationFlag[] {
  const list = Array.isArray(raw) ? raw : arrOf(rec(raw).degradation_flags);
  return list.map((f): DegradationFlag => {
    const r = rec(f);
    const sev = strOr(r.severity, "WATCH").toUpperCase();
    return {
      detected_at: strOrNull(r.detected_at ?? r.detected),
      model_version: strOrNull(r.model_version ?? r.version),
      horizon: numOrNull(r.horizon),
      metric: strOrNull(r.metric),
      baseline: numOrNull(r.baseline),
      current: numOrNull(r.current),
      severity:
        sev === "CRITICAL" || sev === "WARN" || sev === "WATCH"
          ? (sev as DegradationFlag["severity"])
          : "WATCH",
    };
  });
}

function normalizeRegime(raw: unknown): MarketRegime {
  const r = rec(raw);
  return {
    regime: strOr(r.regime, "UNKNOWN"),
    confidence: numOrNull(r.confidence) ?? 0,
    drivers: strArr(r.drivers),
    history: arrOf(r.history).map((h) => {
      const q = rec(h);
      return {
        time: strOr(q.time ?? q.date, ""),
        regime: strOr(q.regime, "UNKNOWN"),
        confidence: numOrNull(q.confidence) ?? 0,
      };
    }),
  };
}

function normalizeRegimeHistory(
  raw: unknown
): { history: RegimeHistoryRow[]; count: number } {
  const r = rec(raw);
  const list = Array.isArray(raw) ? raw : arrOf(r.history);
  const history = list.map((h): RegimeHistoryRow => {
    const q = rec(h);
    return {
      time: strOr(q.time ?? q.date, ""),
      regime: strOr(q.regime, "UNKNOWN"),
      confidence: numOrNull(q.confidence) ?? 0,
      data_status: strOr(q.data_status, "UNCONFIRMED") as DataStatus,
    };
  });
  return { history, count: numOrNull(r.count) ?? history.length };
}

function normalizePortfolio(raw: unknown): PortfolioAnalysis {
  const r = rec(raw);
  const correlationRaw = r.correlation ?? r.correlation_matrix;
  const out: PortfolioAnalysis = {
    data_status: strOr(r.data_status, "UNCONFIRMED") as DataStatus,
    disclaimer: strOr(r.disclaimer, DISCLAIMER),
  };
  const annReturn = numOrNull(r.expected_return_ann ?? r.expected_return);
  if (annReturn !== null) out.expected_return_ann = annReturn;
  const annVol = numOrNull(r.expected_vol_ann ?? r.volatility);
  if (annVol !== null) out.expected_vol_ann = annVol;
  const maxDd = numOrNull(r.max_drawdown);
  if (maxDd !== null) out.max_drawdown = maxDd;
  for (const key of [
    "beta",
    "sharpe",
    "sortino",
    "var_95",
    "cvar_95",
    "diversification_score",
    "sector_hhi",
  ] as const) {
    const n = numOrNull(r[key]);
    if (n !== null) out[key] = n;
  }
  if (correlationRaw && typeof correlationRaw === "object") {
    const corr: Record<string, Record<string, number>> = {};
    for (const [a, row] of Object.entries(rec(correlationRaw))) {
      const inner: Record<string, number> = {};
      for (const [b, v] of Object.entries(rec(row))) {
        const n = numOrNull(v);
        if (n !== null) inner[b] = n;
      }
      corr[a] = inner;
    }
    out.correlation = corr;
  }
  if (r.factor_exposure && typeof r.factor_exposure === "object") {
    const fx: Record<string, number> = {};
    for (const [k, v] of Object.entries(rec(r.factor_exposure))) {
      const n = numOrNull(v);
      if (n !== null) fx[k] = n;
    }
    out.factor_exposure = fx;
  }
  out.stress_tests = arrOf(r.stress_tests).map((s) => {
    const q = rec(s);
    return {
      scenario: strOr(q.scenario, "—"),
      portfolio_return: numOrNull(q.portfolio_return),
      max_drawdown: numOrNull(q.max_drawdown),
      worst_holding: strOrNull(q.worst_holding),
      ...(strOrNull(q.reason) ? { reason: q.reason as string } : {}),
    };
  });
  const asOf = strOrNull(r.as_of);
  if (asOf) out.as_of = asOf;
  return out;
}

function normalizeAlertRule(raw: unknown): AlertRule {
  const r = rec(raw);
  return {
    id: strOr(r.id, "—"),
    rule_type: strOr(r.rule_type, "—"),
    ticker: strOrNull(r.ticker),
    params: rec(r.params) as AlertRule["params"],
    is_active: r.is_active !== false,
  };
}

function normalizeAlert(raw: unknown): AlertItem {
  const r = rec(raw);
  return {
    id: strOr(r.id, "—"),
    created_at: strOr(r.created_at, ""),
    alert_type: strOr(r.alert_type, "—"),
    ticker: strOrNull(r.ticker),
    title: strOr(r.title, "—"),
    body: strOr(r.body, ""),
    severity: strOr(r.severity, "INFO"),
    channel: strOr(r.channel, "IN_APP"),
    read_at: strOrNull(r.read_at),
  };
}

function normalizeResearch(raw: unknown): ResearchAnswer {
  const r = rec(raw);
  return {
    answer: strOr(r.answer, ""),
    citations: arrOf(r.citations).map((c) => {
      const q = rec(c);
      return {
        table: strOr(q.table, "—"),
        id: strOr(q.id, "—"),
        as_of: strOr(q.as_of, ""),
      };
    }),
    data_status: strOr(r.data_status, "UNCONFIRMED") as DataStatus,
    disclaimer: strOr(r.disclaimer, DISCLAIMER),
  };
}

function normalizeBacktest(
  raw: unknown,
  requested: BacktestConfig
): BacktestResult {
  const r = rec(raw);
  const m = rec(r.metrics);
  const metrics: BacktestMetrics = {
    cagr: numOrNull(m.cagr),
    total_return: numOrNull(m.total_return),
    sharpe: numOrNull(m.sharpe),
    sortino: numOrNull(m.sortino),
    max_drawdown: numOrNull(m.max_drawdown),
    win_rate: numOrNull(m.win_rate),
    profit_factor: numOrNull(m.profit_factor),
    expectancy: numOrNull(m.expectancy),
    n_trades: numOrNull(m.n_trades),
    exposure: numOrNull(m.exposure),
    alpha: numOrNull(m.alpha),
    beta: numOrNull(m.beta),
    benchmark: strOrNull(m.benchmark) ?? undefined,
  };
  const equityRaw = Array.isArray(r.equity) ? r.equity : arrOf(r.equity_curve);
  const equity = equityRaw
    .map((e) => {
      const q = rec(e);
      return {
        time: strOr(q.time ?? q.date, ""),
        strategy: numOrNull(q.strategy ?? q.value ?? q.equity) ?? 1,
        benchmark: numOrNull(q.benchmark ?? q.spy) ?? 1,
      };
    })
    .filter((e) => e.time !== "");
  const cfg = rec(r.config);
  const config: BacktestConfig = {
    ...requested,
    name: strOr(cfg.name, requested.name),
    universe:
      typeof cfg.universe === "string" ? cfg.universe : requested.universe,
    start: strOr(cfg.start, requested.start),
    end: strOr(cfg.end, requested.end),
  };
  return {
    equity,
    metrics,
    config,
    data_status: strOr(r.data_status, "UNCONFIRMED") as DataStatus,
    disclaimer: strOr(r.disclaimer, DISCLAIMER),
    notes: strArr(r.notes),
    ...(strOrNull(r.status) ? { status: r.status as string } : {}),
  };
}

/* ------------------------------------------------------------------ fetching */

function demoMeta(error?: DataError | null): Meta {
  const meta: Meta = {
    as_of: DEMO_AS_OF,
    data_status: "MISSING",
    request_id: error ? "demo-fallback" : "demo-synthetic",
    demo: true,
  };
  if (error) meta.error = error;
  return meta;
}

function liveMeta(rawMeta: unknown): Meta {
  const m = rec(rawMeta);
  return {
    as_of: strOr(m.as_of, ""),
    data_status: strOr(m.data_status, "UNCONFIRMED") as DataStatus,
    request_id: strOr(m.request_id, ""),
    demo: false,
  };
}

async function fetchEnvelope<T>(
  path: string,
  demo: () => T,
  normalize: (raw: unknown) => T,
  init?: RequestInit
): Promise<Envelope<T>> {
  if (!API_BASE) {
    return { data: demo(), meta: demoMeta(API_CONFIG_ERROR) };
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${API_BASE}/api/v1${path}`, {
      ...init,
      signal: ctrl.signal,
      cache: "no-store",
    });
    if (!res.ok) {
      throw new ApiProblem(
        "http",
        `HTTP ${res.status}${res.statusText ? ` ${res.statusText}` : ""}`,
        res.status
      );
    }
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      throw new ApiProblem("bad_envelope", "response body was not valid JSON");
    }
    const env = rec(body);
    if (!("data" in env) || !("meta" in env)) {
      throw new ApiProblem(
        "bad_envelope",
        "response did not match the { data, meta } envelope contract"
      );
    }
    if (env.data == null) {
      throw new ApiProblem("empty", "envelope carried null data");
    }
    const data = normalize(env.data);
    const meta = liveMeta(env.meta);
    if (Array.isArray(data) && data.length === 0) meta.empty = true;
    return { data, meta };
  } catch (e) {
    return { data: demo(), meta: demoMeta(classifyError(e)) };
  } finally {
    clearTimeout(timer);
  }
}

/* ---------------------------------------------------------------------- api */

export const api = {
  getAsset: (ticker: string): Promise<Envelope<Asset>> =>
    fetchEnvelope(
      `/assets/${ticker.toUpperCase()}`,
      () => demoAsset(ticker),
      normalizeAsset
    ),

  getPrices: (ticker: string, range = "1y"): Promise<Envelope<PriceBar[]>> =>
    fetchEnvelope(
      `/assets/${ticker.toUpperCase()}/prices?range=${range}&interval=1d`,
      () => demoPrices(ticker),
      normalizeBars
    ),

  getPredictions: (
    ticker: string,
    horizons: number[] = HORIZONS
  ): Promise<Envelope<Prediction[]>> =>
    fetchEnvelope(
      `/predictions/${ticker.toUpperCase()}?horizons=${horizons.join(",")}`,
      () => horizons.map((h) => demoPrediction(ticker, h)),
      normalizePredictions
    ),

  getPrediction: async (
    ticker: string,
    horizon: number
  ): Promise<Envelope<Prediction>> => {
    const env = await fetchEnvelope(
      `/predictions/${ticker.toUpperCase()}?horizons=${horizon}`,
      () => [demoPrediction(ticker, horizon)],
      normalizePredictions
    );
    const first = env.data[0];
    if (env.meta.demo || !first) {
      const error =
        env.meta.error ??
        dataError("empty", `no prediction record for ${ticker} h=${horizon}`);
      return { data: demoPrediction(ticker, horizon), meta: demoMeta(error) };
    }
    return { data: first, meta: env.meta };
  },

  getScanner: (limit = 25): Promise<Envelope<ScannerRow[]>> =>
    fetchEnvelope(
      `/scanner?setup=momentum_breakout&horizon=20&limit=${limit}`,
      () => demoScanner(limit),
      normalizeScanner
    ),

  getAssets: (): Promise<Envelope<Asset[]>> =>
    fetchEnvelope(`/assets?limit=50`, () => demoAssetList(), normalizeAssets),

  getRegime: (): Promise<Envelope<MarketRegime>> =>
    fetchEnvelope(`/market-regime`, () => demoRegime(), normalizeRegime),

  getModels: (): Promise<Envelope<ModelVersionRow[]>> =>
    fetchEnvelope(`/models`, () => demoModels(), normalizeModels),

  getCalibration: (
    version: string,
    horizon: number
  ): Promise<Envelope<CalibrationBucket[]>> =>
    fetchEnvelope(
      `/models/${version}/calibration?horizon=${horizon}`,
      () => demoCalibration(),
      normalizeCalibration
    ),

  getDegradationFlags: (): Promise<Envelope<DegradationFlag[]>> =>
    fetchEnvelope(
      `/models/ARGUS-EQ-1.0/metrics`,
      () => demoDegradationFlags(),
      normalizeFlags
    ),

  /**
   * Dashboard "Today's locked predictions".
   *
   * WHY min_score=50 (and not 70): the API computes
   * composite_score = 100 * (0.70 * p_positive + 0.30 * overlay_p), and measured
   * real rows land at 52–57 (p_positive 0.5577–0.5909). A min_score of 70 was
   * therefore unsatisfiable BY CONSTRUCTION and made this table permanently
   * empty in production. 50 is the neutral / coin-flip point: it filters out
   * sub-coin-flip rows without inventing a threshold the model cannot reach.
   * This is a product choice, not a data-derived cut-off — do not lower it
   * further and do not drop the filter.
   */
  getLockedToday: (): Promise<Envelope<Prediction[]>> =>
    fetchEnvelope(
      `/predictions?horizon=20&min_score=50`,
      () => demoLockedToday(),
      normalizePredictions
    ),

  getRegimeHistory: (
    days = 365
  ): Promise<Envelope<{ history: RegimeHistoryRow[]; count: number }>> =>
    fetchEnvelope(
      `/market-regime/history?days=${days}`,
      () => ({ history: demoRegimeHistory(days), count: days }),
      normalizeRegimeHistory
    ),

  analyzePortfolio: (
    positions: PortfolioPosition[]
  ): Promise<Envelope<PortfolioAnalysis>> =>
    fetchEnvelope(
      `/portfolio/analyze`,
      () => demoPortfolioAnalysis(positions),
      normalizePortfolio,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ positions }),
      }
    ),

  getAlerts: (): Promise<Envelope<AlertItem[]>> =>
    fetchEnvelope(
      `/alerts`,
      () => demoAlerts(),
      (raw) => arrOf(Array.isArray(raw) ? raw : rec(raw).alerts).map(normalizeAlert)
    ),

  getAlertRules: (): Promise<Envelope<AlertRule[]>> =>
    fetchEnvelope(
      `/alert-rules`,
      () => demoAlertRules(),
      (raw) =>
        arrOf(Array.isArray(raw) ? raw : rec(raw).rules).map(normalizeAlertRule)
    ),

  createAlertRule: (
    rule_type: string,
    ticker: string,
    params: Record<string, number | string>
  ): Promise<Envelope<AlertRule>> =>
    fetchEnvelope(
      `/alert-rules`,
      () => demoAlertRules()[0],
      normalizeAlertRule,
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
      normalizeResearch,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      }
    ),

  /**
   * Backtests are heavy compute — always async job based. Demo runs locally.
   * Returns the full envelope so the caller can tell a real result from the
   * synthetic fallback (the previous signature returned a bare result and the
   * page rendered a "demo" badge even for live runs).
   */
  runBacktest: async (
    config: BacktestConfig
  ): Promise<Envelope<BacktestResult>> => {
    const demo = () => demoBacktest(config);
    if (!API_BASE) return { data: demo(), meta: demoMeta(API_CONFIG_ERROR) };
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
    try {
      const res = await fetch(`${API_BASE}/api/v1/backtests`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
        signal: ctrl.signal,
      });
      if (!res.ok)
        throw new ApiProblem(
          "http",
          `HTTP ${res.status}${res.statusText ? ` ${res.statusText}` : ""}`,
          res.status
        );
      const job = rec(await res.json());
      const id = strOrNull(job.backtest_id) ?? strOrNull(job.id);
      if (!id)
        throw new ApiProblem("bad_envelope", "job response carried no id");
      const detail = await fetch(`${API_BASE}/api/v1/backtests/${id}`, {
        cache: "no-store",
      });
      if (!detail.ok)
        throw new ApiProblem(
          "http",
          `HTTP ${detail.status}${detail.statusText ? ` ${detail.statusText}` : ""}`,
          detail.status
        );
      const env = rec(await detail.json());
      if (env.data == null)
        throw new ApiProblem("empty", "backtest detail carried null data");
      return {
        data: normalizeBacktest(env.data, config),
        meta: liveMeta(env.meta),
      };
    } catch (e) {
      return { data: demo(), meta: demoMeta(classifyError(e)) };
    } finally {
      clearTimeout(timer);
    }
  },
};

export { DEFAULT_BACKTEST_CONFIG, DEMO_AS_OF, API_BASE, API_CONFIG_ERROR, TIMEOUT_MS };
export type { BacktestConfig };
