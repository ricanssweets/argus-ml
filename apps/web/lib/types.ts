/** Shared domain types — mirror ~/workspace/argus/docs/API_SPEC.md */

export type DataStatus =
  | "CONFIRMED"
  | "LIKELY"
  | "UNCONFIRMED"
  | "CONFLICTING"
  | "MISSING"
  | "SYNTHETIC_FIXTURE";

export interface Meta {
  as_of: string;
  data_status: DataStatus;
  request_id: string;
  /** Present and true only when the payload is synthetic demo data. */
  demo?: boolean;
}

export interface Envelope<T> {
  data: T;
  meta: Meta;
}

export interface Asset {
  ticker: string;
  name: string;
  asset_type: "STOCK" | "ETF" | "INDEX" | "CRYPTO" | "FUTURE";
  exchange: string;
  currency: string;
  sector: string;
  industry: string;
  price: number;
  change: number;
  change_pct: number;
  mkt_cap: number;
  data_status: DataStatus;
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
  expected_return: number;
  expected_vol: number;
  expected_high: number;
  expected_low: number;
  ci: [number, number];
  downside_risk: number;
  upside_potential: number;
  risk_reward: number;
  composite_score: number; // 0–100
  trend_strength: number;
  momentum_score: number;
  fundamental_score: number;
  risk_score: number;
  volatility_score: number;
  confidence: number; // 0–100, setup quality (NOT probability)
  data_status: DataStatus;
  explanation: {
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
  drivers: string[];
  history: { time: string; regime: string; confidence: number }[];
}

export interface ModelVersionRow {
  version: string;
  status: "EXPERIMENT" | "STAGING" | "CHALLENGER" | "CHAMPION" | "RETIRED";
  horizons: number[];
  brier: number;
  roc_auc: number;
  cal_error: number;
  n: number;
}

export interface CalibrationBucket {
  predicted: number; // mean predicted P per bucket
  observed: number; // observed frequency
  n: number;
}

export interface DegradationFlag {
  detected_at: string;
  model_version: string;
  horizon: number;
  metric: string;
  baseline: number;
  current: number;
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

export interface BacktestResult {
  equity: { time: string; strategy: number; benchmark: number }[];
  metrics: {
    cagr: number;
    total_return: number;
    sharpe: number;
    sortino: number;
    max_drawdown: number;
    win_rate: number;
    profit_factor: number;
    expectancy: number;
    n_trades: number;
    exposure: number;
    alpha: number;
    beta: number;
  };
  config: BacktestConfig;
}
