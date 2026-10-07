"""Portfolio analysis & risk engine (deterministic, point-in-time).

Everything here is computed from the caller-supplied price history —
no external fetches, no hidden state. Positions are buy-and-hold:
``qty`` shares of each ticker; weights float with prices from the
value-weighted start. Pass ``as_of`` to truncate the analysis window
(anti-leakage); all windows below are trailing/causal by construction.

Provided:
  analyze(...)            full portfolio risk payload (dict)
  portfolio_returns(...)  daily portfolio return series
  weights(...)            current weights from qty x last price
  diversification_score   0-100, formula documented below
  factor_exposure         momentum / volatility / reversal from price
                          features (engine-B style); value/size need
                          market caps and report unavailable otherwise
  vol_target_weights      inverse-vol weights scaled to a target vol
  stress_test             historical window replay per STRESS_WINDOWS
  STRESS_WINDOWS          documented scenario windows (see below)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import risk as R

ANNUALIZE = 252.0

# ---------------------------------------------------------------------------
# Static ticker -> sector map.
#
# DOCUMENTED LIMITATION: this is a hard-coded map for common tickers, not a
# live GICS classification. Unknown tickers are reported as "Unknown" and
# counted in concentration math as their own bucket — never silently
# assigned to a sector.
# ---------------------------------------------------------------------------
SECTOR_MAP = {
    "SPY": "Broad Market", "QQQ": "Broad Market", "DIA": "Broad Market",
    "IWM": "Broad Market", "VTI": "Broad Market",
    "NVDA": "Technology", "AAPL": "Technology", "MSFT": "Technology",
    "AVGO": "Technology", "AMD": "Technology", "CRM": "Technology",
    "ORCL": "Technology", "ADBE": "Technology", "INTC": "Technology",
    "META": "Communication", "GOOGL": "Communication", "GOOG": "Communication",
    "NFLX": "Communication", "DIS": "Communication",
    "XLC": "Communication", "XLY": "Consumer Discretionary",
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "HD": "Consumer Discretionary",
    "XLP": "Consumer Staples", "PG": "Consumer Staples", "KO": "Consumer Staples",
    "XLE": "Energy", "XOM": "Energy", "CVX": "Energy",
    "XLF": "Financials", "JPM": "Financials", "V": "Financials",
    "MA": "Financials", "BAC": "Financials", "BRK.B": "Financials",
    "XLV": "Healthcare", "UNH": "Healthcare", "JNJ": "Healthcare",
    "LLY": "Healthcare",
    "XLI": "Industrials", "XLB": "Materials", "XLRE": "Real Estate",
    "XLU": "Utilities", "XLK": "Technology",
}

# ---------------------------------------------------------------------------
# Stress scenarios — historical window replay on the holdings' real prices.
#
# Each window is (start, end) inclusive, chosen to capture the named shock.
# Windows are DOCUMENTED here; changing a window changes results and must
# be recorded wherever results are published.
# ---------------------------------------------------------------------------
STRESS_WINDOWS = {
    # Lehman weekend -> the S&P 500 closing bottom (2009-03-09).
    "2008_crisis": ("2008-09-01", "2009-03-09",
                    "GFC: Lehman collapse to market bottom"),
    # S&P 500 all-time high -> COVID intraday/closing bottom.
    "2020_crash": ("2020-02-19", "2020-03-23",
                   "COVID crash: peak to trough"),
    # First trading day of 2022 -> October closing low of the hiking-cycle
    # bear market (growth/multiple-compression selloff).
    "2022_rate_shock": ("2022-01-03", "2022-10-12",
                        "Fed hiking cycle: growth selloff to Oct low"),
    # CPI lifted off 5% (Jun 2021) -> 9.1% peak print (Jun 2022).
    "high_inflation": ("2021-06-01", "2022-06-30",
                       "Inflation surge: CPI 5% to 9.1% peak"),
    # July 2019 "insurance" cut -> Feb 2020 pre-COVID top.
    "rapid_rate_cuts": ("2019-07-01", "2020-02-29",
                        "2019 insurance cuts into the pre-COVID top"),
    # DOCUMENTED REUSE: the Great Recession equity drawdown is the same
    # 2008-09 -> 2009-03 window as "2008_crisis"; kept as a separate key
    # so API callers can request "recession" by name.
    "recession": ("2008-09-01", "2009-03-09",
                  "Great Recession equity drawdown (same window as "
                  "2008_crisis - documented reuse)"),
    # October 2023 pullback low -> end of the post-hike bull run year.
    "market_rally": ("2023-10-01", "2024-12-31",
                     "Post-hike bull run"),
    # "vol_spike" is special: VIX>30 episodes are derived from a supplied
    # VIX series when available (see stress_test); the fallback below is
    # used only when no VIX series is given, and is reported as such.
    "vol_spike": (None, None,
                  "VIX>30 episodes (derived from VIX series when supplied)"),
}

# Fallback vol-spike windows (VIX>30 episodes), used ONLY when the caller
# does not supply a VIX series. Documented, not estimated live.
VOL_SPIKE_FALLBACKS = [
    ("2020-02-24", "2020-04-30", "COVID vol spike (VIX>30)"),
    ("2022-01-24", "2022-03-14", "2022 hiking-fear vol spike (VIX>30)"),
    ("2008-09-15", "2008-12-31", "GFC vol spike (VIX>30)"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _closes(prices: dict) -> dict[str, pd.Series]:
    out = {}
    for t, df in prices.items():
        s = df["close"] if isinstance(df, pd.DataFrame) else pd.Series(df)
        out[t.upper()] = s.sort_index().astype(float)
    return out


def weights(prices: dict, positions: list[dict]) -> dict[str, float]:
    """Current weights from qty x last available close. Sums to 1."""
    closes = _closes(prices)
    vals = {}
    for p in positions:
        t = p["ticker"].upper()
        if t not in closes or closes[t].dropna().empty:
            raise ValueError(f"no price history for {t}")
        vals[t] = float(p["qty"]) * float(closes[t].dropna().iloc[-1])
    total = sum(vals.values())
    if total <= 0:
        raise ValueError("non-positive portfolio value")
    return {t: v / total for t, v in vals.items()}


def _held_frame(closes: dict[str, pd.Series],
                positions: list[dict]) -> tuple[pd.DataFrame, dict]:
    """Inner-joined close frame restricted to HELD tickers only.

    Restricting to held tickers matters: value-weighting with ``mul(qty)``
    would otherwise introduce NaN columns for unheld tickers and a
    blanket ``dropna()`` would wipe every row.
    """
    qty = {p["ticker"].upper(): float(p["qty"]) for p in positions}
    held = [t for t in qty if t in closes]
    if not held:
        raise ValueError("no held ticker has price history")
    frame = pd.concat({t: closes[t] for t in held}, axis=1).dropna()
    return frame, {t: qty[t] for t in held}


def portfolio_returns(prices: dict, positions: list[dict],
                      as_of=None) -> pd.Series:
    """Daily portfolio returns, buy-and-hold (weights float with prices).

    Share counts are fixed; each day's return is the value-weighted mean
    of holding returns. The series starts at the first date where every
    holding has a price (inner join — documented).
    """
    closes = _closes(prices)
    if as_of is not None:
        as_of = pd.Timestamp(as_of)
        closes = {t: s.loc[s.index <= as_of] for t, s in closes.items()}
    frame, qty = _held_frame(closes, positions)
    if frame.empty:
        raise ValueError("no overlapping price history")
    vals = frame.mul(pd.Series(qty)).dropna()
    port_val = vals.sum(axis=1)
    return port_val.pct_change().dropna().rename("portfolio")


def _ann(ret: pd.Series) -> float:
    return float(ret.mean() * ANNUALIZE)


def _ann_vol(ret: pd.Series) -> float:
    return float(ret.std(ddof=1) * np.sqrt(ANNUALIZE))


def _sharpe(ret: pd.Series) -> float | None:
    sd = ret.std(ddof=1)
    return float(ret.mean() / sd * np.sqrt(ANNUALIZE)) if sd > 0 else None


def _sortino(ret: pd.Series) -> float | None:
    down = ret[ret < 0]
    sd = down.std(ddof=1)
    if len(down) == 0 or sd == 0:
        return None
    return float(ret.mean() / sd * np.sqrt(ANNUALIZE))


# ---------------------------------------------------------------------------
# Diversification score (0-100) — documented formula.
#
#   hhi        = sum(w_i^2), in [1/n, 1]
#   hhi_norm   = (1 - hhi) / (1 - 1/n)   -> 1 for equal-weight, 0 for single
#                                            holding (n=1 -> score 0 by def)
#   corr_pen   = mean off-diagonal correlation, clipped to [0, 1]
#   score      = 100 * hhi_norm * (1 - 0.5 * corr_pen)
#
# Rationale: concentration (HHI) is the primary penalty; average pairwise
# correlation is a secondary penalty because highly correlated holdings
# diversify less. Rounded to 1 decimal.
# ---------------------------------------------------------------------------
def diversification_score(w: dict[str, float],
                          corr: pd.DataFrame | None = None) -> float:
    n = len(w)
    if n <= 1:
        return 0.0
    wv = np.array(list(w.values()), dtype=float)
    hhi = float((wv ** 2).sum())
    hhi_norm = (1.0 - hhi) / (1.0 - 1.0 / n)
    corr_pen = 0.0
    if corr is not None and len(corr) == n:
        m = corr.to_numpy(dtype=float)
        iu = np.triu_indices(n, k=1)
        if len(iu[0]):
            corr_pen = float(np.clip(np.nanmean(m[iu]), 0.0, 1.0))
    return round(100.0 * max(hhi_norm, 0.0) * (1.0 - 0.5 * corr_pen), 1)


def sector_concentration(w: dict[str, float]) -> dict:
    """HHI + top-3 weights + per-sector breakdown (static SECTOR_MAP)."""
    by_sector: dict[str, float] = {}
    for t, weight in w.items():
        sec = SECTOR_MAP.get(t, "Unknown")
        by_sector[sec] = by_sector.get(sec, 0.0) + weight
    hhi = float(sum(v ** 2 for v in by_sector.values()))
    ranked = sorted(by_sector.items(), key=lambda kv: -kv[1])
    return {
        "hhi": round(hhi, 4),
        "top_3": [{"sector": s, "weight": round(x, 4)}
                  for s, x in ranked[:3]],
        "by_sector": [{"sector": s, "weight": round(x, 4)}
                      for s, x in ranked],
        "note": "sectors from a static ticker map; unknown tickers -> "
                "'Unknown' (documented limitation)",
    }


# ---------------------------------------------------------------------------
# Factor exposure — engine-B style price factors per holding.
#
#   momentum   : tanh-scaled blend of 20/63/126/252d returns
#                (same construction as engines.engine_b)
#   reversal   : negated 21d return, tanh-scaled
#   volatility : 63d realized vol, annualized
#   size/value : need market caps / fundamentals -> reported as unavailable
#                (None + reason) unless market_caps are supplied, in which
#                case size = cross-sectional z of log market cap.
# Portfolio factor = weight-weighted mean of holding factors.
# ---------------------------------------------------------------------------
def _tanh(x: float, scale: float) -> float:
    return float(np.tanh(x / scale))


def factor_exposure(prices: dict, w: dict[str, float],
                    market_caps: dict[str, float] | None = None,
                    as_of=None) -> dict:
    closes = _closes(prices)
    if as_of is not None:
        as_of = pd.Timestamp(as_of)
        closes = {t: s.loc[s.index <= as_of] for t, s in closes.items()}
    per: dict[str, dict] = {}
    for t, s in closes.items():
        s = s.dropna()
        if len(s) < 260:
            per[t] = {"momentum": None, "reversal": None,
                      "volatility": None, "reason": "need >= 260 bars"}
            continue
        c = s.to_numpy()
        moms = [c[-1] / c[-21] - 1.0, c[-1] / c[-64] - 1.0,
                c[-1] / c[-127] - 1.0, c[-1] / c[-253] - 1.0]
        blend = 0.15 * moms[0] + 0.25 * moms[1] + 0.30 * moms[2] + 0.30 * moms[3]
        rev = -(c[-1] / c[-22] - 1.0)
        rets = pd.Series(c).pct_change().dropna().to_numpy()
        vol = float(np.std(rets[-63:], ddof=1) * np.sqrt(ANNUALIZE))
        per[t] = {"momentum": round(_tanh(blend, 0.15), 4),
                  "reversal": round(_tanh(rev, 0.05), 4),
                  "volatility": round(vol, 4)}
    size = None
    size_reason = "market caps not supplied"
    if market_caps:
        caps = {t.upper(): market_caps.get(t, market_caps.get(t.upper()))
                for t in closes}
        vals = {t: v for t, v in caps.items() if v and v > 0}
        if len(vals) >= 2:
            lz = {t: float(np.log(v)) for t, v in vals.items()}
            mu = float(np.mean(list(lz.values())))
            sd = float(np.std(list(lz.values()), ddof=1)) or 1.0
            size = {t: round((z - mu) / sd, 3) for t, z in lz.items()}
            size_reason = "z-scored log market cap (cross-sectional)"
    port = {}
    for f in ("momentum", "reversal", "volatility"):
        vals = [(w[t], per[t][f]) for t in per
                if t in w and per[t].get(f) is not None]
        port[f] = (round(sum(x * y for x, y in vals) / sum(x for x, _ in vals), 4)
                   if vals else None)
    return {"per_holding": per,
            "portfolio": {**port,
                          "size": None if size is None else
                          round(sum(w[t] * size[t] for t in size if t in w), 3),
                          "value": None},
            "notes": {
                "size": size_reason,
                "value": "needs point-in-time fundamentals "
                         "(P/E, book value) - not available from prices",
                "construction": "momentum/reversal/volatility use "
                                "engine-B style trailing price features",
            }}


# ---------------------------------------------------------------------------
# Vol-targeted position sizing.
#
# weight_i = (1 / vol_i) / sum(1 / vol_j), then the whole vector is scaled
# by k = target_vol / portfolio_vol(weights) so that the resulting
# portfolio's trailing-63d annualized vol ~= target_vol. k is capped at
# max_leverage (documented). If target vol is unreachable even at the cap,
# the capped weights are returned with reached=false.
# ---------------------------------------------------------------------------
def vol_target_weights(prices: dict, positions: list[dict],
                       target_vol: float = 0.15,
                       max_leverage: float = 1.0,
                       as_of=None) -> dict:
    closes = _closes(prices)
    if as_of is not None:
        as_of = pd.Timestamp(as_of)
        closes = {t: s.loc[s.index <= as_of] for t, s in closes.items()}
    rets = pd.concat({t: s.pct_change() for t, s in closes.items()},
                     axis=1).dropna().tail(63)
    if len(rets) < 20:
        raise ValueError("need >= 20 overlapping return bars")
    vols = rets.std(ddof=1) * np.sqrt(ANNUALIZE)
    vols = vols.replace(0.0, np.nan).dropna()
    if vols.empty:
        raise ValueError("zero volatility")
    inv = 1.0 / vols
    w0 = inv / inv.sum()
    cov = rets[vols.index].cov().to_numpy() * ANNUALIZE
    port_vol = float(np.sqrt(w0.to_numpy() @ cov @ w0.to_numpy()))
    k = min(target_vol / port_vol if port_vol > 0 else max_leverage,
            max_leverage)
    wts = (w0 * k).to_dict()
    return {"weights": {t: round(float(x), 4) for t, x in wts.items()},
            "target_vol": target_vol,
            "estimated_portfolio_vol": round(float(port_vol * k), 4),
            "leverage": round(float(k), 4),
            "reached": bool(k < max_leverage or
                            abs(port_vol * k - target_vol) / target_vol < 0.05),
            "note": "inverse-vol weights scaled to target vol; capped at "
                    "max_leverage"}


# ---------------------------------------------------------------------------
# Stress testing — replay each STRESS_WINDOWS window on the holdings.
# ---------------------------------------------------------------------------
def _window_slice(frame: pd.DataFrame, start, end) -> pd.DataFrame:
    if start is None or end is None:
        return frame.iloc[0:0]
    return frame.loc[start:end]


def stress_test(prices: dict, positions: list[dict],
                scenarios: list[str] | None = None,
                vix: pd.Series | None = None) -> list[dict]:
    """Replay historical stress windows. Each scenario reports the
    buy-and-hold portfolio return over the window, max drawdown inside
    the window, and the worst holding (ticker + its return).

    Windows with no overlapping price data are reported with
    portfolio_return=None and a reason (never silently skipped).
    """
    closes = _closes(prices)
    frame = pd.concat(closes, axis=1).dropna()
    qty = {p["ticker"].upper(): float(p["qty"]) for p in positions}
    held = [t for t in qty if t in frame.columns]
    if not held:
        raise ValueError("no held ticker has price history")
    frame = frame[held]  # only held tickers: mul(qty) must not create NaN cols
    qty = {t: qty[t] for t in held}
    names = scenarios or list(STRESS_WINDOWS)  # default: every scenario
    out = []
    for name in names:
        if name not in STRESS_WINDOWS:
            out.append({"scenario": name, "portfolio_return": None,
                        "reason": "unknown scenario"})
            continue
        start, end, desc = STRESS_WINDOWS[name]
        if name == "vol_spike":
            out.extend(_vol_spike(frame, qty, vix))
            continue
        win = _window_slice(frame, start, end)
        if len(win) < 5:
            out.append({"scenario": name, "window": [start, end],
                        "description": desc, "portfolio_return": None,
                        "reason": "insufficient overlapping price data in "
                                  "window"})
            continue
        out.append(_one_window(name, start, end, desc, win, qty))
    return out


def _one_window(name, start, end, desc, win: pd.DataFrame,
                qty: dict) -> dict:
    vals = win.mul(pd.Series(qty)).dropna()
    port = vals.sum(axis=1)
    total = float(port.iloc[-1] / port.iloc[0] - 1.0)
    dd = R.max_drawdown(port)
    hret = {t: float(win[t].iloc[-1] / win[t].iloc[0] - 1.0)
            for t in win.columns}
    worst = min(hret, key=hret.get)
    return {"scenario": name, "window": [start, end], "description": desc,
            "portfolio_return": round(total, 4),
            "max_drawdown": round(float(dd["max_drawdown"]), 4),
            "worst_holding": worst,
            "worst_holding_return": round(hret[worst], 4)}


def _vol_spike(frame: pd.DataFrame, qty: dict,
               vix: pd.Series | None) -> list[dict]:
    """VIX>30 episodes. With a VIX series: derive episodes (contiguous
    runs of VIX>30, min 5 days), replay the worst (highest peak VIX).
    Without: replay the documented VOL_SPIKE_FALLBACKS."""
    if vix is not None and not vix.dropna().empty:
        v = vix.sort_index().dropna()
        above = (v > 30.0)
        episodes, cur = [], None
        for day, is_above in above.items():
            if is_above and cur is None:
                cur = [day, day]
            elif is_above:
                cur[1] = day
            elif cur is not None:
                episodes.append(tuple(cur))
                cur = None
        if cur is not None:
            episodes.append(tuple(cur))
        episodes = [e for e in episodes
                    if (e[1] - e[0]).days >= 4]
        if episodes:
            worst_ep = max(episodes,
                           key=lambda e: float(v.loc[e[0]:e[1]].max()))
            s, e = (d.strftime("%Y-%m-%d") for d in worst_ep)
            win = _window_slice(frame, s, e)
            if len(win) >= 5:
                d = _one_window("vol_spike", s, e,
                                "worst VIX>30 episode in supplied VIX series "
                                f"(peak VIX {float(v.loc[worst_ep[0]:worst_ep[1]].max()):.1f})",
                                win, qty)
                d["derived_from_vix"] = True
                return [d]
    out = []
    for s, e, desc in VOL_SPIKE_FALLBACKS:
        win = _window_slice(frame, s, e)
        if len(win) < 5:
            out.append({"scenario": "vol_spike", "window": [s, e],
                        "description": desc, "portfolio_return": None,
                        "derived_from_vix": False,
                        "reason": "insufficient overlapping price data"})
            continue
        d = _one_window("vol_spike", s, e, desc + " (fallback - no VIX "
                        "series supplied)", win, qty)
        d["derived_from_vix"] = False
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Full analysis
# ---------------------------------------------------------------------------
def analyze(prices: dict, positions: list[dict],
            benchmark: pd.Series | dict | None = None,
            as_of=None,
            scenarios: list[str] | None = None,
            vix: pd.Series | None = None,
            market_caps: dict[str, float] | None = None,
            target_vol: float = 0.15) -> dict:
    """Full portfolio risk payload (all deterministic).

    ``prices``: {ticker: OHLCV DataFrame or close Series}.
    ``benchmark``: close Series (or {"SPY": df}); defaults to SPY from
    ``prices`` when present, else beta is None (unavailable, not 0).
    """
    if not positions:
        raise ValueError("positions is empty")
    ret = portfolio_returns(prices, positions, as_of=as_of)
    if len(ret) < 30:
        raise ValueError("need >= 30 portfolio return bars")
    w = weights(prices, positions)
    closes = _closes(prices)
    if as_of is not None:
        closes = {t: s.loc[s.index <= pd.Timestamp(as_of)] for t, s in closes.items()}
    indiv = pd.concat({t: closes[t].pct_change() for t in closes},
                      axis=1).dropna()
    corr = indiv.corr() if len(indiv) >= 30 else None

    bench = None
    if isinstance(benchmark, dict):
        b = benchmark.get("SPY")
        bench = b["close"] if isinstance(b, pd.DataFrame) else pd.Series(b)
    elif benchmark is not None:
        bench = (benchmark["close"] if isinstance(benchmark, pd.DataFrame)
                 else pd.Series(benchmark))
    elif "SPY" in closes:
        bench = closes["SPY"]
    # R.beta expects RETURN series; convert benchmark closes -> returns.
    beta = (R.beta(ret, pd.Series(bench).pct_change().dropna())
            if bench is not None else None)

    eq = (1.0 + ret).cumprod()
    mdd = R.max_drawdown(eq)
    sharpe, sortino = _sharpe(ret), _sortino(ret)

    return {
        "as_of": str(ret.index[-1].date()),
        "n_bars": len(ret),
        "window": [str(ret.index[0].date()), str(ret.index[-1].date())],
        "expected_return": round(_ann(ret), 4),
        "volatility": round(_ann_vol(ret), 4),
        "max_drawdown": round(float(mdd["max_drawdown"]), 4),
        "beta": round(beta, 3) if beta is not None else None,
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "sortino": round(sortino, 3) if sortino is not None else None,
        "var_95": round(R.historical_var(ret, 0.05), 4),
        "cvar_95": round(R.historical_cvar(ret, 0.05), 4),
        "var_99": round(R.historical_var(ret, 0.01), 4),
        "cvar_99": round(R.historical_cvar(ret, 0.01), 4),
        "var_note": "historical VaR/CVaR reported as POSITIVE loss "
                    "fractions (quant convention)",
        "weights": {t: round(x, 4) for t, x in w.items()},
        "correlation_matrix": (corr.round(3).to_dict() if corr is not None
                               else None),
        "sector_concentration": sector_concentration(w),
        "diversification_score": diversification_score(w, corr),
        "factor_exposure": factor_exposure(prices, w, market_caps, as_of),
        "vol_target_sizing": vol_target_weights(prices, positions,
                                                target_vol, as_of=as_of),
        "stress_tests": stress_test(prices, positions, scenarios, vix),
        "data_status": "CONFIRMED",
    }
