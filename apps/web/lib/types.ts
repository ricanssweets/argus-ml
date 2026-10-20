/** Shared domain types — mirror ~/workspace/argus/docs/API_SPEC.md */

export type DataStatus =
  | "CONFIRMED"
  | "LIKELY"
  | "UNCONFIRMED"
  | "CONFLICTING"
  | "MISSING"
  | "SYNTHETIC_FIXTURE";

/**
 * Why a live fetch did not deliver data. Recorded by lib/api.ts so the UI can
 * distinguish "API down" / "401" / "500" / "timeout" / "no records" from a
 * success instead of silently rendering synthetic numbers as if real.
 */
export type DataErrorKind =
  | "config" // NEXT_PUBLIC_ARGUS_API_URL missing in a production build
  | "network" // DNS / connection refused / offline
  | "timeout" // AbortController fired
  | "http" // non-2xx status
  | "bad_envelope" // body did not match { data, meta }
  | "empty" // envelope present but the record set was empty where one was required
  | "unknown";

export interface DataError {
  kind: DataErrorKind;
  /** HTTP status when kind === "http". */
  status?: number;
  message: string;
}

export interface Meta {
  as_of: string;
  data_status: DataStatus;
  request_id: string;
  /** Present and true only when the payload is synthetic demo data. */
  demo?: boolean;
  /** Present only when the live call failed; the payload is then synthetic. */
  error?: DataError;
  /** True when the API answered successfully with an empty record set. */
  empty?: boolean;
}

export interface Envelope<T> {
  data: T;
  meta: Meta;
}

export interface Asset {
  ticker: string;
  name: string | null;
  asset_type: "STOCK" | "ETF" | "INDEX" | "CRYPTO" | "FUTURE";
  exchange: string | null;
  currency: string | null;
  sector: string | null;
  industry: string | null;
  /** Nullable: the live API (AssetDetail) omits price when no bar is stored. */
  price: number | null;
  prev_close?: number | null;
  change: number | null;
  /**
   * API CONTRACT (services/ml/app/routers/assets.py:45): `change_pct` is
   * ALREADY a percent — 1.23 means +1.23%, NOT a fraction. Never pass it to
   * fraction-based formatters such as ret1(); use retPct1() (lib/format.ts).
   */
  change_pct: number | null;
  mkt_cap: number | null;
  /** The live API names this field `market_cap`; api.ts mirrors both. */
  market_cap?: number | null;
  data_status: DataStatus;
  disclaimer?: string;
}

export interface PriceBar {
  time: string; // ISO date
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Prediction {
  ticker: string;
  horizon: number; // trading days
  as_of: string;
  model_version: string;
  regime: string;
  p_positive: number;
  p_negative: number;
  /** All optional: the live API marks them Optional[...] (schemas.py Prediction). */
  expected_return?: number | null;
  expected_vol?: number | null;
  expected_high?: number | null;
  expected_low?: number | null;
  ci?: number[] | null;
  downside_risk?: number | null;
  upside_potential?: number | null;
  risk_reward?: number | null;
  composite_score: number; // 0–100
  trend_strength?: number | null;
  momentum_score?: number | null;
  fundamental_score?: number | null;
  risk_score?: number | null;
  volatility_score?: number | null;
  confidence: number; // 0–100, setup quality (NOT probability)
  data_status: DataStatus;
  explanation?: {
    reasons: string[];
    risks: string[];
    invalidated_if: string[];
  };
  /** Per-engine detail (live pipeline). Absent on synthetic demo records. */
  engine_signals?: Record<
    string,
    { signal: number; confidence: number; data_status: DataStatus; note: string }
  >;
  /** "live" = real 8-engine pipeline, "synthetic_stub" = demo fallback. */
  pipeline?: string;
  /** True when the isotonic calibration map was applied. */
  calibrated?: boolean;
  disclaimer: string;
}

export interface ScannerRow {
  ticker: string;
  name: string;
  sector: string;
  score: number;
  p_positive: number;
  expected_return: number;
  risk_reward: number;
  setup: string;
  horizon: number;
}

export interface MarketRegime {
  regime: string;
  confidence: number; // 0–1
  /** Optional: the live API defaults both to [] and may omit them. */
  drivers?: string[];
  history?: { time: string; regime: string; confidence: number }[];
}

/**
 * Model registry row. The live /models endpoint returns MODEL_METADATA only —
 * brier / roc_auc / cal_error / n are frequently absent (schemas.py
 * ModelVersionInfo has none of them), so every field beyond version/status is
 * optional and every consumer must guard before formatting.
 */
export interface ModelVersionRow {
  version: string;
  status: "EXPERIMENT" | "STAGING" | "CHALLENGER" | "CHAMPION" | "RETIRED";
  horizons?: number[];
  brier?: number | null;
  roc_auc?: number | null;
  /** Canonical name used by this UI. */
  cal_error?: number | null;
  /** Name used by the live ModelMetrics schema; api.ts mirrors it into cal_error. */
  calibration_error?: number | null;
  n?: number | null;
  family?: string;
  weights_are_priors?: boolean;
  data_status?: DataStatus;
}

/**
 * Canonical calibration bucket used by the chart and table. api.ts normalizes
 * every API variant ({bin,n,mean_predicted,observed_rate} and/or
 * {predicted,observed}) into these fields; null means "not provided" and must
 * never become NaN in the chart.
 */
export interface CalibrationBucket {
  /** Mean predicted probability for the bin (from `mean_predicted` or `predicted`). */
  predicted: number | null;
  /** Observed frequency (from `observed_rate` or `observed`). */
  observed: number | null;
  /** Sample count. */
  n: number | null;
  /** Raw bucket edges [lo, hi] when the API provides `bin`. */
  bin?: [number, number] | null;
}

export interface DegradationFlag {
  detected_at?: string | null;
  model_version?: string | null;
  horizon?: number | null;
  metric?: string | null;
  baseline?: number | null;
  current?: number | null;
  severity: "WATCH" | "WARN" | "CRITICAL";
}

export interface RegimeHistoryRow {
  time: string;
  regime: string;
  confidence: number;
  data_status: DataStatus;
}

export interface PortfolioPosition {
  ticker: string;
  qty: number;
}

export interface PortfolioAnalysis {
  expected_return_ann?: number;
  expected_vol_ann?: number;
  max_drawdown?: number;
  beta?: number;
  sharpe?: number;
  sortino?: number;
  var_95?: number;
  cvar_95?: number;
  diversification_score?: number;
  sector_hhi?: number;
  correlation?: Record<string, Record<string, number>>;
  factor_exposure?: Record<string, number>;
  stress_tests?: {
    scenario: string;
    portfolio_return: number | null;
    max_drawdown: number | null;
    worst_holding: string | null;
    reason?: string;
  }[];
  data_status: DataStatus;
  as_of?: string;
  disclaimer: string;
}

export interface AlertRule {
  id: string;
  rule_type: string;
  ticker?: string | null;
  params: Record<string, number | string>;
  is_active: boolean;
}

export interface AlertItem {
  id: string;
  created_at: string;
  alert_type: string;
  ticker?: string | null;
  title: string;
  body: string;
  severity: string;
  channel: string;
  read_at?: string | null;
}

export interface ResearchCitation {
  table: string;
  id: string;
  as_of: string;
}

export interface ResearchAnswer {
  answer: string;
  citations: ResearchCitation[];
  data_status: DataStatus;
  disclaimer: string;
}

export interface BacktestConfig {
  name: string;
  universe: string;
  start: string;
  end: string;
  stop_loss: number; // %
  take_profit: number; // %
  slippage_bps: number;
  commission: number; // $ per trade
  allow_short: boolean;
}

export interface BacktestMetrics {
  cagr?: number | null;
  total_return?: number | null;
  sharpe?: number | null;
  sortino?: number | null;
  max_drawdown?: number | null;
  win_rate?: number | null;
  profit_factor?: number | null;
  expectancy?: number | null;
  n_trades?: number | null;
  exposure?: number | null;
  alpha?: number | null;
  beta?: number | null;
  benchmark?: string;
}

export interface BacktestResult {
  equity: { time: string; strategy: number; benchmark: number }[];
  /** Optional: the live API returns Optional[BacktestMetrics]. */
  metrics?: BacktestMetrics | null;
  config: BacktestConfig;
  data_status?: DataStatus;
  disclaimer?: string;
  notes?: string[];
  status?: string;
}
