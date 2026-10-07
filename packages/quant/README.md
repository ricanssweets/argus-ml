# argus-quant

Deterministic quantitative library for the **ARGUS** prediction platform
(`packages/quant`). Importable as `argus_quant`.

Locked laws (from `docs/ARCHITECTURE.md`):

- **No future leak.** Every indicator/feature at bar `t` uses bars `<= t`
  only. Enforced by test (`tests/test_no_leakage.py`), not convention.
- **Calibration is fit on out-of-fold predictions only** — the calibrator
  API takes `oof_predictions`, never training arrays.
- **Minimum evidence:** `MIN_SAMPLES_PER_BUCKET = 20` for any calibration
  bucket or performance claim.
- **Honesty about data:** engines report `data_status`
  (`CONFIRMED | LIKELY | UNCONFIRMED | CONFLICTING | MISSING`); missing data
  is reported, never fabricated.

## Install

```bash
python -m venv .venv && .venv/bin/pip install -e .
# or: pip install -r requirements.txt
```

Dependencies: stdlib + `pandas` + `numpy` + `scikit-learn` (+ `pytest` for
tests). `xgboost`/`lightgbm` are optional-only (try/except), never required.

## Usage

```python
import pandas as pd
from argus_quant import indicators as ind
from argus_quant.features import build_features
from argus_quant import engines, validation, metrics, calibration, backtest, montecarlo, risk

# df: OHLCV DataFrame with DatetimeIndex (columns open/high/low/close/volume)
rsi = ind.rsi(df["close"], 14)            # Wilder RSI, causal, NaN warmup
macd = ind.macd(df["close"])              # macd / macd_signal / macd_hist

# Point-in-time features: truncated to bars <= as_of before computing
feats, fhash, snapped = build_features(df, "2026-10-05")
print(fhash)  # sha256 of canonical feature JSON - reproducibility key

# Engine sub-signals in [-1, 1]
out = engines.run_engines(df, "2026-10-05", peers={"SPY": spy_df})
print(out["A"])  # {"signal":..., "confidence":..., "data_status":..., "details":...}

# Walk-forward splits + leakage assertion
for tr, te in validation.walk_forward_splits(n=1000, train_window=252, test_window=21):
    ...
validation.assert_no_leakage(X_times, label_starts, label_ends)  # raises on leak

# Metrics (calibration first)
metrics.brier_score(y_true, y_prob)
table = metrics.calibration_table(y_true, y_prob, n_bins=10)  # merges <20-sample bins

# Calibration (OOF only)
cal = calibration.IsotonicCalibrator().fit(oof_pred, oof_labels)
p = cal.predict(raw_scores)
cal.to_map()   # -> calibration_maps DB row shape {"method","buckets","fitted_at","min_samples"}

# Backtest
res = backtest.run_backtest(
    df, entry_rule, exit_rule,
    position_fraction=0.10, stop_loss=0.05, take_profit=0.10,
    slippage_bps=1.0, commission=1.0, allow_short=False,
    spy_prices=spy_close,
)
print(res["sharpe"], res["max_drawdown"], res["n_trades"])

# Monte Carlo distribution (50k residual-bootstrap paths, seeded)
dist = montecarlo.residual_bootstrap(returns, expected_return=0.05, horizon=20, seed=42)

# Risk
risk.historical_var(returns, alpha=0.05)
risk.historical_cvar(returns, alpha=0.05)
risk.fixed_fractional_size(equity=100_000, risk_pct=0.01, entry=150.0, stop=140.0)
```

## Phase 4 — Engines C (Fundamental), D (Macro), F (Options), G (Sentiment)

Real logic on free data sources; Engine E (regime) remains a documented stub
for Phase 5.

### Data sources & access

| Engine | Source | Key / auth | Connector |
|---|---|---|---|
| C | SEC EDGAR companyfacts + submissions | none; **User-Agent header required** (`ArgusResearch contact@example.com`) — SEC returns 403 without it | `data_connectors/edgar.py` |
| D | FRED `fredgraph.csv` (FEDFUNDS, DGS10, DGS2, CPIAUCSL, UNRATE, BAMLH0A0HYM2, DTWEXBGS, DCOILWTICO) | none | `data_connectors/fred.py` |
| F | yfinance option chains | none; delayed ~15 min, throttled — any failure → engine reports MISSING | `data_connectors/options.py` |
| G | EDGAR 8-K filings + optional headline list | none; deterministic finance lexicon (~40 pos / ~40 neg words, documented in `sentiment.py`) | `data_connectors/sentiment.py` |

### Cache layout (`~/workspace/argus/data/`)

```
data/
  edgar/company_tickers.json        # ticker -> CIK (30d TTL)
  edgar/facts/CIK{cik:010d}.json    # companyfacts (7d TTL)
  edgar/submissions/CIK{cik:010d}.json  # filings list (1d TTL)
  fred/{SERIES}.csv                 # raw FRED CSVs (1d TTL)
  options/{TICKER}_{YYYY-MM-DD}.json  # chain snapshots (per-day)
  options/iv_history/{TICKER}.csv   # daily ATM-IV history (builds over time)
  sector_medians.json               # built by scripts/build_sector_medians.py
```

All connectors sleep ≥ 0.25 s between requests and are cache-first.

### Lookbacks & point-in-time rules

- **EDGAR:** the knowledge date is the fact's `filed` date — `asof_*`
  helpers return only facts with `filed <= as_of` (facts without a filed
  date are excluded). TTM flow items = latest 4 *discrete* quarters
  (duration 70–120 days; YTD contexts sharing the same (fy, fp) are
  excluded to avoid double-counting), else latest annual. Balance-sheet
  items = latest knowable point. When several tags exist for one concept,
  the tag with the most recently filed data wins (stale duplicates like
  AAPL's legacy `Revenues` never win silently).
- **FRED:** every feature uses observations `<= as_of`. `real_10y` needs
  13 CPI months; `inflation_surprise` 24; `hy_spread_z` 252 daily obs;
  `dollar_trend`/`oil_trend` 63 trading days.
- **Options:** chain snapshot date must be `<= as_of` or it is rejected.
  IV rank/percentile need ≥ 20 ATM-IV history points — never faked from a
  single observation.
- **Sentiment:** 8-K lookback 180d, headlines 90d; momentum 28d vs prior
  28d; velocity = 7d doc-count z-score vs trailing 90d.

### Honesty notes

- Engine F is **always** `data_status="LIKELY"` with the note *"delayed
  chain, not real-time"* — never presented as real-time.
- Engine G with < 5 documents caps confidence at 0.4; with 0 documents it
  reports MISSING.
- Every engine returns MISSING (signal 0.0) instead of fabricating when
  its inputs are absent — including when yfinance is unreachable.
- Engine C blends sector-relative valuation (cheap = +) and quality
  (high ROE/margins/FCF yield, low leverage = +) percentiles against
  `data/sector_medians.json` (50 large caps, 5 sectors); without a sector
  context it falls back to hurdle scoring; loss-making P/E is excluded
  from valuation, never scored as "cheap".

### Smoke test

```bash
.venv/bin/python scripts/smoke_phase4.py   # NVDA + AAPL as_of 2026-10-05
```

## Tests

```bash
.venv/bin/pytest -q
```

All fixtures are **synthetic** (seeded RNG), clearly labeled in
`tests/conftest.py` — never real market data.

## Module map

| Module | Contents |
|---|---|
| `indicators` | SMA, EMA, RSI (Wilder), MACD, Stochastic, ADX, ATR (Wilder), Bollinger, Keltner, Ichimoku, VWAP, OBV — all causal |
| `features` | `build_features(df, as_of)` + `FEATURE_SPECS` registry (lookback per feature) + `feature_hash` |
| `engines` | A (technical), B (factors), C (fundamentals: sector-percentile or hurdle, graceful MISSING), D (macro tailwind/headwind from FRED), F (options, delayed-chain LIKELY), G (lexicon sentiment), H (cross-asset); E (regime) documented stub |
| `validation` | walk-forward splitter, purged K-fold + embargo, `assert_no_leakage` |
| `metrics` | brier, log-loss, ROC-AUC, accuracy/precision/recall/F1, MAE/RMSE, directional accuracy, calibration table (min-20 rule) |
| `calibration` | `IsotonicCalibrator`, `PlattCalibrator` — fit on OOF only; `to_map`/`from_map` for `calibration_maps` |
| `backtest` | event-driven, entry/exit callables, stop-loss/take-profit, fractional sizing, slippage, commission, long/short, SPY benchmark |
| `montecarlo` | residual bootstrap → P(positive), E[R], vol, high/low, CI, downside/upside, risk-reward |
| `risk` | historical VaR/CVaR, fixed-fractional + volatility-targeted sizing, max drawdown |
| `data_connectors` | `edgar` (SEC facts/submissions, PIT-safe), `fred` (macro CSVs), `options` (delayed yfinance chains), `sentiment` (finance lexicon) |
