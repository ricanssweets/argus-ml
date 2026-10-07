"""Portfolio risk primitives: historical VaR/CVaR, position sizing,
max-drawdown accounting. Deterministic; no hidden state."""

from __future__ import annotations

import numpy as np
import pandas as pd


def historical_var(returns, alpha: float = 0.05) -> float:
    """Historical VaR at tail probability ``alpha`` (e.g. 0.05 => 95% VaR).

    Reported as a POSITIVE loss fraction: VaR=0.03 means "losses exceed 3%
    only (1-alpha) of the time"... precisely: the alpha-quantile loss.
    """
    r = np.asarray(pd.Series(returns).dropna(), dtype=float)
    if len(r) == 0:
        raise ValueError("empty returns")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0,1)")
    return float(-np.quantile(r, alpha))


def historical_cvar(returns, alpha: float = 0.05) -> float:
    """Historical CVaR (expected shortfall): mean loss conditional on the
    loss exceeding VaR. Positive loss fraction."""
    r = np.asarray(pd.Series(returns).dropna(), dtype=float)
    if len(r) == 0:
        raise ValueError("empty returns")
    var = -historical_var(r, alpha)
    tail = r[r <= var]
    if len(tail) == 0:
        return float(-var)
    return float(-tail.mean())


def max_drawdown(equity_or_returns, is_returns: bool = False) -> dict:
    """Max drawdown with peak/trough locations.

    Pass an equity curve (``is_returns=False``) or a return series
    (``is_returns=True``, cumulated internally). Returns
    {"max_drawdown", "peak_idx", "trough_idx", "recovery_idx" or None}.
    """
    s = pd.Series(equity_or_returns).dropna()
    eq = (1.0 + s).cumprod() if is_returns else s
    peak = eq.cummax()
    dd = (eq - peak) / peak.replace(0.0, np.nan)
    trough = dd.idxmin()
    max_dd = float(dd.min())
    peak_idx = eq.loc[:trough].idxmax()
    rec = dd.loc[trough:]
    recovered = rec[rec >= 0]
    recovery_idx = recovered.index[0] if len(recovered) else None
    return {"max_drawdown": round(max_dd, 6),
            "peak_idx": peak_idx, "trough_idx": trough,
            "recovery_idx": recovery_idx}


def beta(asset_returns, benchmark_returns) -> float | None:
    """CAPM beta of an asset vs a benchmark: cov(r_a, r_b) / var(r_b).

    Series are inner-joined on index; needs >= 60 overlapping bars,
    else None (unavailable, never 0). Deterministic.
    """
    a = pd.Series(asset_returns).dropna()
    b = pd.Series(benchmark_returns).dropna()
    common = a.index.intersection(b.index)
    if len(common) < 60:
        return None
    a, b = a.loc[common].astype(float), b.loc[common].astype(float)
    var_b = float(b.var(ddof=1))
    if var_b <= 0:
        return None
    return float(a.cov(b) / var_b)


def fixed_fractional_size(equity: float, risk_pct: float, entry: float,
                          stop: float) -> dict:
    """Fixed-fractional shares: risk ``risk_pct`` of equity on the distance
    entry->stop. Returns {"shares", "risk_amount", "notional"}."""
    if equity <= 0 or entry <= 0:
        raise ValueError("equity and entry must be positive")
    if not 0.0 < risk_pct < 1.0:
        raise ValueError("risk_pct must be in (0,1)")
    per_share_risk = abs(entry - stop)
    if per_share_risk <= 0:
        raise ValueError("stop must differ from entry")
    risk_amount = equity * risk_pct
    shares = int(risk_amount // per_share_risk)
    return {"shares": shares, "risk_amount": round(risk_amount, 2),
            "notional": round(shares * entry, 2)}


def volatility_targeted_size(equity: float, returns, target_vol: float,
                             price: float, annualize: float = 252.0,
                             lookback: int = 63, max_leverage: float = 1.0
                             ) -> dict:
    """Size so the position's annualized vol ~= target_vol (fraction of
    equity). Caps notional at ``max_leverage`` x equity."""
    r = np.asarray(pd.Series(returns).dropna().tail(lookback), dtype=float)
    if len(r) < 20:
        raise ValueError("need at least 20 returns")
    if price <= 0 or equity <= 0:
        raise ValueError("price and equity must be positive")
    asset_vol = float(np.std(r, ddof=1) * np.sqrt(annualize))
    if asset_vol <= 0:
        raise ValueError("asset volatility is zero")
    weight = min(target_vol / asset_vol, max_leverage)
    notional = equity * weight
    return {"shares": int(notional // price),
            "weight": round(weight, 4),
            "notional": round(int(notional // price) * price, 2),
            "asset_vol": round(asset_vol, 4)}
