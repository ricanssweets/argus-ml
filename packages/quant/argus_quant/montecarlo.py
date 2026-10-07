"""Monte Carlo distribution via residual bootstrap.

Method (per ARCHITECTURE.md step 10): resample residuals
``r_t - mean(r)`` from the trailing return history, then build paths as
``cumprod(1 + expected_return/h + residual_t)``. 50k paths by default,
seeded RNG for reproducibility (same seed + same inputs => identical
outputs).

Outputs the prediction distribution record fields: P(positive),
E[R], vol, expected high/low, CI, downside/upside, risk-reward.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def residual_bootstrap(returns, expected_return: float, horizon: int = 20,
                       n_paths: int = 50_000, seed: int = 42) -> dict:
    """Bootstrap the return distribution over ``horizon`` bars.

    ``returns``: trailing 1-bar simple returns (a Series or array) used
    ONLY as the residual pool. ``expected_return``: the calibrated expected
    total return over the horizon (drift per bar = expected_return/horizon).

    Returns dict with p_positive, expected_return, expected_vol,
    expected_high, expected_low, ci_lower, ci_upper, downside_risk,
    upside_potential, risk_reward.
    """
    r = np.asarray(pd.Series(returns).dropna(), dtype=float)
    if len(r) < 20:
        raise ValueError("need at least 20 returns for the residual pool")
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    rng = np.random.default_rng(seed)

    residuals = r - r.mean()
    drift = expected_return / horizon
    draws = rng.choice(residuals, size=(n_paths, horizon))
    path_rets = drift + draws
    terminal = np.prod(1.0 + path_rets, axis=1) - 1.0
    # path high/low: max/min cumulative return along each path
    cum = np.cumprod(1.0 + path_rets, axis=1) - 1.0
    path_high = cum.max(axis=1)
    path_low = cum.min(axis=1)

    pos = terminal[terminal > 0]
    neg = terminal[terminal < 0]
    downside = float(-neg.mean()) if len(neg) else 0.0
    upside = float(pos.mean()) if len(pos) else 0.0
    risk_reward = upside / downside if downside > 0 else (
        float("inf") if upside > 0 else 0.0)

    return {
        "p_positive": round(float(np.mean(terminal > 0)), 4),
        "p_negative": round(float(np.mean(terminal < 0)), 4),
        "expected_return": round(float(np.mean(terminal)), 4),
        "expected_vol": round(float(np.std(terminal, ddof=1)), 4),
        "expected_high": round(float(np.mean(path_high)), 4),
        "expected_low": round(float(np.mean(path_low)), 4),
        "ci_lower": round(float(np.quantile(terminal, 0.05)), 4),
        "ci_upper": round(float(np.quantile(terminal, 0.95)), 4),
        "downside_risk": round(downside, 4),
        "upside_potential": round(upside, 4),
        "risk_reward": round(risk_reward, 4)
        if np.isfinite(risk_reward) else None,
        "n_paths": n_paths,
        "horizon": horizon,
        "seed": seed,
        "note": "residual bootstrap; not a guarantee of future performance",
    }
