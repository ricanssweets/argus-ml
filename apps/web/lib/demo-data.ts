/**
 * SYNTHETIC DEMO DATA — every value here is generated deterministically from
 * a seeded PRNG. None of it is real market data. The UI must display a
 * prominent "DEMO DATA" badge whenever this module is the source.
 */
import type {
  AlertItem,
  AlertRule,
  Asset,
  BacktestConfig,
  BacktestResult,
  CalibrationBucket,
  DegradationFlag,
  MarketRegime,
  ModelVersionRow,
  PortfolioAnalysis,
  PortfolioPosition,
  Prediction,
  PriceBar,
  RegimeHistoryRow,
  ResearchAnswer,
  ScannerRow,
} from "./types";
import { DISCLAIMER } from "./format";

export const SYNTHETIC = true;
export const DEMO_AS_OF = "2026-10-05T20:00:00Z";

/* ---------- deterministic PRNG (mulberry32) ---------- */

export function hashSeed(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function tradingDaysBack(n: number, endISO: string): string[] {
  const out: string[] = [];
  const d = new Date(endISO);
  while (out.length < n) {
    const dow = d.getUTCDay();
    if (dow !== 0 && dow !== 6) out.push(d.toISOString().slice(0, 10));
    d.setUTCDate(d.getUTCDate() - 1);
  }
  return out.reverse();
}

/* ---------- assets ---------- */

const ASSET_DEFS: Omit<Asset, "price" | "change" | "change_pct">[] = [
  { ticker: "NVDA", name: "NVIDIA Corporation", asset_type: "STOCK", exchange: "NASDAQ", currency: "USD", sector: "Technology", industry: "Semiconductors", mkt_cap: 3.42e12, data_status: "CONFIRMED" },
  { ticker: "AAPL", name: "Apple Inc.", asset_type: "STOCK", exchange: "NASDAQ", currency: "USD", sector: "Technology", industry: "Consumer Electronics", mkt_cap: 3.61e12, data_status: "CONFIRMED" },
  { ticker: "MSFT", name: "Microsoft Corporation", asset_type: "STOCK", exchange: "NASDAQ", currency: "USD", sector: "Technology", industry: "Software", mkt_cap: 3.18e12, data_status: "CONFIRMED" },
  { ticker: "META", name: "Meta Platforms, Inc.", asset_type: "STOCK", exchange: "NASDAQ", currency: "USD", sector: "Technology", industry: "Social Media", mkt_cap: 1.44e12, data_status: "CONFIRMED" },
  { ticker: "AMD", name: "Advanced Micro Devices, Inc.", asset_type: "STOCK", exchange: "NASDAQ", currency: "USD", sector: "Technology", industry: "Semiconductors", mkt_cap: 2.61e11, data_status: "CONFIRMED" },
  { ticker: "TSLA", name: "Tesla, Inc.", asset_type: "STOCK", exchange: "NASDAQ", currency: "USD", sector: "Consumer Cyclical", industry: "Auto Manufacturers", mkt_cap: 8.12e11, data_status: "CONFIRMED" },
  { ticker: "QQQ", name: "Invesco QQQ Trust", asset_type: "ETF", exchange: "NASDAQ", currency: "USD", sector: "Technology", industry: "ETF — Large Cap Growth", mkt_cap: 3.05e11, data_status: "CONFIRMED" },
  { ticker: "SPY", name: "SPDR S&P 500 ETF Trust", asset_type: "ETF", exchange: "NYSE", currency: "USD", sector: "Broad Market", industry: "ETF — Large Cap Blend", mkt_cap: 6.48e11, data_status: "CONFIRMED" },
];

const BASE_PRICES: Record<string, number> = {
  NVDA: 178.42, AAPL: 256.18, MSFT: 514.63, META: 682.15,
  AMD: 162.77, TSLA: 434.90, QQQ: 604.31, SPY: 668.44,
};

export function demoAsset(ticker: string): Asset {
  const t = ticker.toUpperCase();
  const def =
    ASSET_DEFS.find((a) => a.ticker === t) ?? {
      ticker: t,
      name: `${t} (synthetic)`,
      asset_type: "STOCK" as const,
      exchange: "NASDAQ",
      currency: "USD",
      sector: "Unknown",
      industry: "—",
      mkt_cap: 1.0e10,
      data_status: "MISSING" as const,
    };
  const rng = mulberry32(hashSeed("asset:" + t));
  const base = BASE_PRICES[t] ?? 50 + rng() * 200;
  const changePct = (rng() - 0.45) * 0.06;
  const price = base * (1 + changePct * 0.2);
  return {
    ...def,
    price,
    change: price * changePct,
    change_pct: changePct,
  };
}

export function demoAssetList(): Asset[] {
  return ASSET_DEFS.map((a) => demoAsset(a.ticker));
}

/* ---------- price series ---------- */

export function demoPrices(ticker: string, days = 252): PriceBar[] {
  const t = ticker.toUpperCase();
  const rng = mulberry32(hashSeed("prices:" + t));
  const dates = tradingDaysBack(days, "2026-10-05");
  const drift = 0.0006 + rng() * 0.0008;
  const vol = 0.016 + rng() * 0.014;
  let px = (BASE_PRICES[t] ?? 100) / Math.exp(drift * days * 0.6);
  const bars: PriceBar[] = [];
  for (const time of dates) {
    const r = drift + (rng() + rng() + rng() - 1.5) * vol * 0.82;
    const open = px;
    const close = px * Math.exp(r);
    const high = Math.max(open, close) * (1 + rng() * vol * 0.5);
    const low = Math.min(open, close) * (1 - rng() * vol * 0.5);
    bars.push({
      time,
      open,
      high,
      low,
      close,
      volume: Math.round(20e6 * (0.5 + rng() * 1.5)),
    });
    px = close;
  }
  return bars;
}

export function movingAverage(bars: PriceBar[], n: number): (number | null)[] {
  return bars.map((_, i) => {
    if (i < n - 1) return null;
    let s = 0;
    for (let k = i - n + 1; k <= i; k++) s += bars[k].close;
    return s / n;
  });
}

/* ---------- predictions ---------- */

export const HORIZONS = [1, 3, 5, 10, 20, 63, 126, 252];
export const HORIZON_LABELS: Record<number, string> = {
  1: "1 day", 3: "3 days", 5: "5 days", 10: "10 days",
  20: "20 days", 63: "3 months", 126: "6 months", 252: "12 months",
};

const REASONS = [
  "Trend score positive: price above 50d and 200d moving averages with rising ADX.",
  "Momentum breadth improving: 63% of sector peers closed above their 20d highs.",
  "Options flow: call/put volume ratio 1.8x with elevated near-term call skew.",
  "Macro backdrop constructive: regime model classifies current tape as Bull with 78% confidence.",
  "Factor exposure: positive loading on quality and momentum factors this quarter.",
  "Volatility compression: 20d realized vol below 60th percentile — favorable for drift continuation.",
];
const RISKS = [
  "Mean-reversion risk if RSI(14) pushes above 75 into overbought territory.",
  "Earnings event inside the horizon window raises gap risk.",
  "Crowded positioning: elevated short interest could amplify downside on a miss.",
  "Macro sensitivity: a 1σ VIX spike historically cuts the edge by ~40%.",
];
const INVALIDATED = [
  "A daily close below the 50d moving average breaks the trend thesis.",
  "Regime flips to Bear with ≥70% confidence — probabilities must be re-run.",
  "Realized 20d volatility exceeds the 90th percentile of the calibration window.",
];

export function demoPrediction(ticker: string, horizon: number): Prediction {
  const t = ticker.toUpperCase();
  const rng = mulberry32(hashSeed(`pred:${t}:${horizon}`));
  const pick = (arr: string[], k: number) => {
    const idx = new Set<number>();
    while (idx.size < k) idx.add(Math.floor(rng() * arr.length));
    return Array.from(idx).map((i) => arr[i]);
  };
  const pPos = 0.45 + rng() * 0.28;
  const erScale = Math.sqrt(horizon) / Math.sqrt(20);
  const er = (0.02 + rng() * 0.09) * erScale * (rng() > 0.28 ? 1 : -0.6);
  const vol = 0.24 * erScale;
  const ciW = 1.64 * vol * 0.5;
  const score = Math.round(42 + rng() * 50);
  const conf = Math.round(58 + rng() * 32);
  const ENGINES = [
    "Technical",
    "Factors",
    "Fundamentals",
    "Macro",
    "Options",
    "Sentiment",
    "CrossAsset",
  ] as const;
  const engine_signals: Prediction["engine_signals"] = {};
  for (const e of ENGINES) {
    const missing = rng() > 0.82;
    engine_signals[e] = {
      signal: missing ? 0 : +(rng() * 2 - 1).toFixed(2),
      confidence: missing ? 0 : +(0.4 + rng() * 0.6).toFixed(2),
      data_status: missing ? "MISSING" : e === "Options" ? "LIKELY" : "CONFIRMED",
      note: missing ? "DEMO: no coverage" : "DEMO: synthetic engine detail",
    };
  }
  return {
    ticker: t,
    horizon,
    as_of: DEMO_AS_OF,
    model_version: "ARGUS-EQ-1.0",
    regime: rng() > 0.25 ? "Bull" : "Neutral",
    p_positive: pPos,
    p_negative: 1 - pPos,
    expected_return: er,
    expected_vol: vol,
    expected_high: er + ciW * 0.7,
    expected_low: er - ciW * 0.7,
    ci: [er - ciW, er + ciW],
    downside_risk: Math.abs(er - ciW) * 0.55,
    upside_potential: er + ciW * 0.62,
    risk_reward: 1.4 + rng() * 2.2,
    composite_score: score,
    trend_strength: Math.round(30 + rng() * 65),
    momentum_score: Math.round(30 + rng() * 65),
    fundamental_score: Math.round(35 + rng() * 60),
    risk_score: Math.round(25 + rng() * 60),
    volatility_score: Math.round(30 + rng() * 60),
    confidence: conf,
    data_status: "UNCONFIRMED",
    engine_signals,
    pipeline: "synthetic_stub",
    calibrated: false,
    explanation: {
      reasons: pick(REASONS, 3),
      risks: pick(RISKS, 2),
      invalidated_if: pick(INVALIDATED, 2),
    },
    disclaimer: DISCLAIMER,
  };
}

export function demoPredictions(
  ticker: string,
  horizons: number[] = HORIZONS
): Prediction[] {
  return horizons.map((h) => demoPrediction(ticker, h));
}

/* ---------- scanner ---------- */

const SETUPS = [
  "momentum_breakout",
  "mean_reversion",
  "trend_continuation",
  "volatility_compression",
  "factor_rotation",
];

export function demoScanner(limit = 25): ScannerRow[] {
  return demoAssetList()
    .slice(0, Math.min(limit, ASSET_DEFS.length))
    .map((a) => {
      const rng = mulberry32(hashSeed("scan:" + a.ticker));
      const p = demoPrediction(a.ticker, 20);
      return {
        ticker: a.ticker,
        name: a.name,
        sector: a.sector,
        score: p.composite_score,
        p_positive: p.p_positive,
        expected_return: p.expected_return,
        risk_reward: p.risk_reward,
        setup: SETUPS[Math.floor(rng() * SETUPS.length)],
        horizon: 20,
      };
    })
    .sort((x, y) => y.score - x.score);
}

/* ---------- market regime ---------- */

const REGIMES = ["Bull", "Neutral", "Bear", "HighVol"];

export function demoRegime(): MarketRegime {
  const rng = mulberry32(hashSeed("regime"));
  const history: MarketRegime["history"] = [];
  const dates = tradingDaysBack(126, "2026-10-05");
  let regime = "Neutral";
  for (const time of dates) {
    if (rng() < 0.12) regime = REGIMES[Math.floor(rng() * REGIMES.length)];
    history.push({
      time,
      regime,
      confidence: 0.55 + rng() * 0.4,
    });
  }
  return {
    regime: "Bull",
    confidence: 0.78,
    drivers: [
      "SPY above 200d MA with expanding breadth (72% of constituents above 50d).",
      "VIX term structure in contango — no stress pricing.",
      "Credit spreads (HYG/IEF) stable at 22nd percentile.",
      "Momentum factor 20d return +3.1%, top decile of 5y history.",
    ],
    history,
  };
}

/* ---------- observatory ---------- */

export function demoModels(): ModelVersionRow[] {
  const rng = mulberry32(hashSeed("models"));
  const rows: ModelVersionRow[] = [
    {
      version: "ARGUS-EQ-1.0",
      status: "CHAMPION",
      horizons: HORIZONS,
      brier: 0.212,
      roc_auc: 0.571,
      cal_error: 0.037,
      n: 12482,
    },
    {
      version: "ARGUS-EQ-1.1",
      status: "CHALLENGER",
      horizons: HORIZONS,
      brier: 0.208,
      roc_auc: 0.576,
      cal_error: 0.041,
      n: 3180,
    },
    {
      version: "ARGUS-EQ-0.9",
      status: "RETIRED",
      horizons: HORIZONS,
      brier: 0.229,
      roc_auc: 0.552,
      cal_error: 0.052,
      n: 18934,
    },
  ].map((m) => ({
    ...m,
    brier: m.brier + (rng() - 0.5) * 0.004,
    status: m.status as ModelVersionRow["status"],
  }));
  return rows;
}

export function demoCalibration(): CalibrationBucket[] {
  const rng = mulberry32(hashSeed("calib"));
  const buckets: CalibrationBucket[] = [];
  for (let i = 0; i < 10; i++) {
    const predicted = 0.05 + i * 0.1;
    const observed = Math.min(
      0.98,
      Math.max(0.02, predicted + (rng() - 0.5) * 0.06)
    );
    buckets.push({
      predicted,
      observed: Math.round(observed * 1000) / 1000,
      n: 320 + Math.floor(rng() * 1400),
    });
  }
  return buckets;
}

export function demoDegradationFlags(): DegradationFlag[] {
  return [
    {
      detected_at: "2026-10-04T06:00:00Z",
      model_version: "ARGUS-EQ-1.0",
      horizon: 1,
      metric: "calibration_error",
      baseline: 0.034,
      current: 0.051,
      severity: "WARN",
    },
    {
      detected_at: "2026-10-02T06:00:00Z",
      model_version: "ARGUS-EQ-1.0",
      horizon: 63,
      metric: "brier",
      baseline: 0.218,
      current: 0.233,
      severity: "WATCH",
    },
    {
      detected_at: "2026-09-28T06:00:00Z",
      model_version: "ARGUS-EQ-0.9",
      horizon: 20,
      metric: "roc_auc",
      baseline: 0.552,
      current: 0.521,
      severity: "CRITICAL",
    },
  ];
}

/* ---------- backtest (deterministic from config) ---------- */

export function demoBacktest(cfg: BacktestConfig): BacktestResult {
  const rng = mulberry32(hashSeed("backtest:" + JSON.stringify(cfg)));
  const start = new Date(cfg.start + "T00:00:00Z");
  const end = new Date(cfg.end + "T00:00:00Z");
  const months: string[] = [];
  const d = new Date(start);
  while (d <= end) {
    months.push(d.toISOString().slice(0, 7) + "-01");
    d.setUTCMonth(d.getUTCMonth() + 1);
  }
  let s = 1,
    b = 1;
  const equity = months.map((time) => {
    const sr = 0.011 + (rng() - 0.46) * 0.075; // strategy edge
    const br = 0.008 + (rng() - 0.5) * 0.06; // SPY-ish
    s *= 1 + sr;
    b *= 1 + br;
    return { time, strategy: s, benchmark: b };
  });

  const rets = equity.slice(1).map((e, i) => e.strategy / equity[i].strategy - 1);
  const mean = rets.reduce((a, x) => a + x, 0) / rets.length;
  const sd = Math.sqrt(
    rets.reduce((a, x) => a + (x - mean) ** 2, 0) / rets.length
  );
  const down = rets.filter((r) => r < 0);
  const downSd = Math.sqrt(
    down.reduce((a, x) => a + x * x, 0) / Math.max(1, down.length)
  );
  let peak = 1,
    maxDD = 0;
  for (const e of equity) {
    peak = Math.max(peak, e.strategy);
    maxDD = Math.min(maxDD, e.strategy / peak - 1);
  }
  const wins = rets.filter((r) => r > 0);
  const losses = rets.filter((r) => r < 0);
  const avgWin =
    wins.reduce((a, x) => a + x, 0) / Math.max(1, wins.length);
  const avgLoss = Math.abs(
    losses.reduce((a, x) => a + x, 0) / Math.max(1, losses.length)
  );
  const years =
    (end.getTime() - start.getTime()) / (365.25 * 24 * 3600 * 1000);

  return {
    equity,
    metrics: {
      cagr: Math.pow(s, 1 / Math.max(0.1, years)) - 1,
      total_return: s - 1,
      sharpe: (mean / Math.max(1e-9, sd)) * Math.sqrt(12),
      sortino: (mean / Math.max(1e-9, downSd)) * Math.sqrt(12),
      max_drawdown: maxDD,
      win_rate: wins.length / Math.max(1, rets.length),
      profit_factor: (avgWin * wins.length) / Math.max(1e-9, avgLoss * losses.length),
      expectancy: mean,
      n_trades: 120 + Math.floor(rng() * 400),
      exposure: 0.55 + rng() * 0.3,
      alpha: 0.02 + rng() * 0.05,
      beta: 0.7 + rng() * 0.5,
    },
    config: cfg,
  };
}

export const DEFAULT_BACKTEST_CONFIG: BacktestConfig = {
  name: "momentum_breakout_20d",
  universe: "SP500",
  start: "2021-01-01",
  end: "2026-09-30",
  stop_loss: 8,
  take_profit: 25,
  slippage_bps: 5,
  commission: 1.0,
  allow_short: false,
};

/* ---------- today's locked predictions ---------- */

export function demoLockedToday(): Prediction[] {
  return ["NVDA", "AAPL", "MSFT", "AMD", "QQQ"].map((t) =>
    demoPrediction(t, 20)
  );
}

/* ---------- regime history ---------- */

export function demoRegimeHistory(days = 365): RegimeHistoryRow[] {
  const rng = mulberry32(hashSeed("regime-history"));
  const regs = ["Bull", "Bull", "Neutral", "Strong Bull", "Risk-Off", "High Vol"];
  const rows: RegimeHistoryRow[] = [];
  const d = new Date("2026-10-05T00:00:00Z");
  for (let i = days - 1; i >= 0; i--) {
    const dt = new Date(d.getTime() - i * 86400000);
    rows.push({
      time: dt.toISOString().slice(0, 10),
      regime: regs[Math.floor(rng() * regs.length)],
      confidence: Math.round(40 + rng() * 50),
      data_status: "UNCONFIRMED",
    });
  }
  return rows;
}

/* ---------- portfolio ---------- */

export function demoPortfolioAnalysis(
  positions: PortfolioPosition[]
): PortfolioAnalysis {
  const rng = mulberry32(hashSeed(`portfolio:${positions.map((p) => p.ticker).join(",")}`));
  const tickers = positions.map((p) => p.ticker);
  const correlation: Record<string, Record<string, number>> = {};
  for (const a of tickers) {
    correlation[a] = {};
    for (const b of tickers) {
      correlation[a][b] = a === b ? 1 : +(0.35 + rng() * 0.5).toFixed(2);
    }
  }
  return {
    expected_return_ann: +(0.06 + rng() * 0.08).toFixed(4),
    expected_vol_ann: +(0.16 + rng() * 0.1).toFixed(4),
    max_drawdown: -(0.12 + rng() * 0.15),
    beta: +(0.9 + rng() * 0.5).toFixed(3),
    sharpe: +(0.5 + rng() * 0.9).toFixed(3),
    sortino: +(0.6 + rng() * 1.0).toFixed(3),
    var_95: -(0.02 + rng() * 0.02),
    cvar_95: -(0.03 + rng() * 0.03),
    diversification_score: Math.round(55 + rng() * 35),
    sector_hhi: +(0.2 + rng() * 0.4).toFixed(3),
    correlation,
    factor_exposure: {
      momentum: +(rng() * 2 - 1).toFixed(2),
      value: +(rng() * 2 - 1).toFixed(2),
      volatility: +(rng() * 2 - 1).toFixed(2),
      size: +(rng() * 2 - 1).toFixed(2),
    },
    stress_tests: [
      { scenario: "2008-style crisis", portfolio_return: -0.38, max_drawdown: -0.45, worst_holding: tickers[0] ?? "—" },
      { scenario: "2020 crash", portfolio_return: -0.22, max_drawdown: -0.28, worst_holding: tickers[0] ?? "—" },
      { scenario: "2022 rate shock", portfolio_return: -0.18, max_drawdown: -0.24, worst_holding: tickers[0] ?? "—" },
    ],
    data_status: "UNCONFIRMED",
    as_of: DEMO_AS_OF,
    disclaimer: DISCLAIMER,
  };
}

/* ---------- alerts ---------- */

export function demoAlertRules(): AlertRule[] {
  return [
    { id: "demo-rule-1", rule_type: "score_change", ticker: "NVDA", params: { min_delta: 10 }, is_active: true },
    { id: "demo-rule-2", rule_type: "breakout", ticker: "AAPL", params: {}, is_active: true },
  ];
}

export function demoAlerts(): AlertItem[] {
  return [
    {
      id: "demo-alert-1",
      created_at: DEMO_AS_OF,
      alert_type: "breakout",
      ticker: "NVDA",
      title: "DEMO: NVDA breakout",
      body: "Synthetic demo alert — not a real signal.",
      severity: "info",
      channel: "in_app",
      read_at: null,
    },
  ];
}

/* ---------- research ---------- */

export function demoResearch(question: string): ResearchAnswer {
  return {
    answer: `DEMO: no platform data is backing this answer (synthetic fallback). Your question was: "${question}". Connect the API for real research answers with citations.`,
    citations: [],
    data_status: "UNCONFIRMED",
    disclaimer: DISCLAIMER,
  };
}
