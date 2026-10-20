"""Deterministic signal engines A-H.

Every engine returns a dict::
    {"signal": float in [-1, 1],
     "confidence": float in [0, 1],
     "data_status": "CONFIRMED" | "LIKELY" | "UNCONFIRMED" | "CONFLICTING" | "MISSING",
     "details": {...}}

Engines A, B, H are fully implemented on price data.
Engine C (fundamentals) scores sector-relative percentiles when a
sector_context (from data/sector_medians.json) is supplied, else falls
back to hurdle-based scoring; MISSING when no usable fundamentals.
Engine D (macro) scores FRED macro features; Engine F (options) scores
delayed option-chain features (always LIKELY, never real-time);
Engine G (sentiment) scores lexicon polarity of 8-K filings + headlines.
Engine E (regime) is a DOCUMENTED STUB: signal 0.0, confidence 0.0,
data_status "MISSING" with reason "unavailable - wire in later phase".
No values are fabricated.

All engines are deterministic: same inputs -> same outputs, no RNG.
"""

from __future__ import annotations

import json
import logging
import os

import numpy as np
import pandas as pd

from . import indicators as ind
from .features import build_features

log = logging.getLogger("argus_quant.engines")

STUB_REASON = "unavailable - wire in later phase"

# Data directory for the optional JSON side-files (sector medians, ...).
# Configured by environment variable, defaulting to ``data/`` INSIDE the
# package -- never a developer home directory. A configured-but-missing
# path is logged, so the silent degradation to hurdle scoring is visible.
DATA_DIR = os.path.expanduser(
    os.environ.get("ARGUS_QUANT_DATA_DIR")
    or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
SECTOR_MEDIANS_PATH = os.path.expanduser(
    os.environ.get("ARGUS_QUANT_SECTOR_MEDIANS")
    or os.path.join(DATA_DIR, "sector_medians.json"))


def _clip(x: float) -> float:
    return float(max(-1.0, min(1.0, x)))


def _safe(v) -> float:
    try:
        f = float(v)
        return f if np.isfinite(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _tanh_scale(x: float, scale: float = 1.0) -> float:
    return float(np.tanh(x / scale))


# ---------------------------------------------------------------------------
# Engine A — Technical (indicators)
# ---------------------------------------------------------------------------
_A_COMPONENTS = ["rsi", "macd", "stoch", "trend", "bollinger", "ichimoku",
                 "volume"]


def engine_a(df: pd.DataFrame, as_of) -> dict:
    """Technical sub-signal from causal indicators at ``as_of``.

    Components (each in [-1,1], weighted):
      rsi        : mean-reversion read ((50 - rsi)/50 clipped)
      macd       : sign/scale of histogram
      stoch      : mean-reversion read ((50 - %K)/50 clipped)
      trend      : +1 if close>SMA50 and SMA20>SMA50, -1 mirrored, scaled by ADX
      bollinger  : mean-reversion read from %B ((0.5 - %B)*2 clipped)
      ichimoku   : cloud position (+1/0/-1)
      volume     : volume z-score signed by the trend read

    MISSING-INPUT CONTRACT (mirrors engine B): a component whose *raw*
    indicator is NaN contributes 0.0 with weight 0.0 -- it is never turned
    into a value by a zero-fill. ``confidence`` is the fraction of the 7
    components with valid (non-NaN) inputs and ``data_status`` is
    CONFIRMED only when all 7 are valid, LIKELY at >= 5, UNCONFIRMED below
    that, and MISSING (signal 0.0, confidence 0.0) when no input is
    usable. Insufficient history therefore produces a neutral read, never
    a fabricated bullish one.
    """
    feats, _, snapped = build_features(df, as_of)
    f = feats.iloc[0]
    parts, weights, valid = [], [], 0

    def add(value, weight):
        """Add a component; weight 0 and value 0.0 when the input is NaN."""
        nonlocal valid
        try:
            ok = value is not None and bool(np.isfinite(float(value)))
        except (TypeError, ValueError):
            ok = False
        if ok:
            parts.append(_clip(float(value)))
            weights.append(weight)
            valid += 1
        else:
            parts.append(0.0)
            weights.append(0.0)

    add((50.0 - f["rsi_14"]) / 50.0, 0.20)          # RSI mean reversion
    add(_tanh_scale(f["macd_hist"] * 200.0), 0.15)  # MACD momentum
    add((50.0 - f["stoch_k"]) / 50.0, 0.10)         # stochastic

    cv, sc, adx_v = f["close_vs_sma50"], f["sma20_sma50_cross"], f["adx_14"]
    trend_dir = None
    if pd.notna(cv) and pd.notna(sc):
        trend_dir = _clip(float(cv) * 10.0 + float(sc) * 10.0)
        if pd.notna(adx_v) and float(adx_v) > 0:
            add(trend_dir * min(float(adx_v) / 25.0, 1.0), 0.20)
        else:
            # ADX unavailable: keep the real trend read, ungated (25% scale)
            add(trend_dir * 0.25, 0.20)
    else:
        add(None, 0.20)

    add((0.5 - f["bb_pctb"]) * 2.0, 0.10)           # bollinger reversion
    add(f["ichimoku_cloud"], 0.15)                  # cloud
    # Volume is signed by the trend read; a FLAT trend (trend_dir == 0.0)
    # has no sign, so the component contributes exactly 0.0 -- the old
    # ``np.sign(trend_dir or 1.0)`` treated 0.0 as falsy and pushed
    # anomalous volume BULLISH in a sideways tape. An unknown trend
    # (trend_dir is None) drops the component instead of assuming +1.
    if pd.notna(f["volume_zscore_20"]) and trend_dir is not None:
        add(_clip(float(f["volume_zscore_20"]) * 0.1)
            * float(np.sign(trend_dir)), 0.10)
    else:
        add(None, 0.10)

    n_components = len(_A_COMPONENTS)
    wsum = sum(weights)
    signal = _clip(sum(p * w for p, w in zip(parts, weights)) / wsum) if wsum > 0 else 0.0
    confidence = round(valid / n_components, 3)
    if valid == 0:
        status = "MISSING"
    elif valid == n_components:
        status = "CONFIRMED"
    elif valid >= 5:
        status = "LIKELY"
    else:
        status = "UNCONFIRMED"
    components = {n: round(p, 4) for n, p in zip(_A_COMPONENTS, parts)}
    details = {
        "as_of": str(snapped),
        "components": components,
        "valid_components": valid,
        "missing_components": [n for n, w in zip(_A_COMPONENTS, weights) if w == 0.0],
        "note": "all indicators causal; values at as_of use bars <= as_of only; "
                "components with NaN inputs carry weight 0 (no zero-fill)",
    }
    if valid == 0:
        details["reason"] = ("no usable indicator at as_of (insufficient "
                            "history) - neutral, not fabricated")
    return {
        "signal": signal,
        "confidence": confidence,
        "data_status": status,
        "details": details,
    }


# ---------------------------------------------------------------------------
# Engine B — Factors (momentum / reversal / volatility from price data)
# ---------------------------------------------------------------------------
def engine_b(df: pd.DataFrame, as_of) -> dict:
    """Factor sub-signal from price-derived factors.

    Components:
      momentum   : tanh-scaled blend of 20/63/126/252d returns
      reversal   : short-term reversal (negated 21d return)
      vol_penalty: high vol_ratio (>1.3) dampens the momentum read
    Confidence: fraction of valid momentum horizons.
    """
    feats, _, snapped = build_features(df, as_of)
    f = feats.iloc[0]
    moms = [_safe(f["momentum_20d"]), _safe(f["momentum_63d"]),
            _safe(f["momentum_126d"]), _safe(f["momentum_252d"])]
    valid_m = sum(1 for n in ["momentum_20d", "momentum_63d",
                              "momentum_126d", "momentum_252d"]
                  if pd.notna(f[n]))
    mom_blend = 0.15 * moms[0] + 0.25 * moms[1] + 0.30 * moms[2] + 0.30 * moms[3]
    momentum = _tanh_scale(mom_blend, scale=0.15)
    reversal = _tanh_scale(_safe(f["reversal_1m"]), scale=0.05)
    vol_ratio = _safe(f["vol_ratio_20_63"])
    vol_penalty = min(max((vol_ratio - 1.0) / 0.5, 0.0), 1.0) if vol_ratio > 0 else 0.0
    signal = _clip(0.70 * momentum + 0.20 * reversal - 0.10 * vol_penalty * np.sign(momentum))
    confidence = round(valid_m / 4, 3)
    status = "CONFIRMED" if valid_m == 4 else ("LIKELY" if valid_m >= 2 else "UNCONFIRMED")
    return {
        "signal": signal,
        "confidence": confidence,
        "data_status": status,
        "details": {
            "as_of": str(snapped),
            "momentum_blend": round(mom_blend, 4),
            "momentum": round(momentum, 4),
            "reversal": round(reversal, 4),
            "vol_ratio_20_63": round(vol_ratio, 4),
            "valid_horizons": valid_m,
        },
    }


# ---------------------------------------------------------------------------
# Engine C — Fundamentals (sector-relative when context supplied)
# ---------------------------------------------------------------------------
def load_sector_context(sector: str, path: str = SECTOR_MEDIANS_PATH) -> dict | None:
    """Load {sector, as_of, distributions} for percentile scoring.

    Returns None when the file is absent or the sector has no data --
    engine C then falls back to hurdle scoring (or MISSING). A missing
    file is LOGGED (the path is configuration, not a silent default), so
    "sector context never loaded" is diagnosable instead of invisible.
    """
    if not os.path.exists(path):
        log.warning("sector medians file not found at %s - engine C falls "
                    "back to hurdle scoring; set ARGUS_QUANT_DATA_DIR or "
                    "ARGUS_QUANT_SECTOR_MEDIANS", path)
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        log.warning("sector medians file at %s is unreadable - engine C "
                    "falls back to hurdle scoring", path, exc_info=True)
        return None
    dists = data.get("sectors", {}).get(sector)
    if not dists:
        return None
    return {"sector": sector, "as_of": data.get("as_of"),
            "distributions": dists}


def _percentile(value: float, dist: list) -> float | None:
    """Fraction of distribution <= value (mid-rank for ties), in [0,1]."""
    dist = sorted(v for v in dist if v is not None)
    n = len(dist)
    if n == 0:
        return None
    less = sum(1 for v in dist if v < value)
    eq = sum(1 for v in dist if v == value)
    return (less + 0.5 * eq) / n


# Sector-relative metric groups for engine C.
# valuation: cheap (low percentile) -> positive. Negative P/E or EV/EBITDA
#   (loss-making) are excluded from valuation, never scored as "cheap".
# quality:   high ROE/FCF-yield/margin percentile -> positive;
#            low debt/equity percentile -> positive.
_C_VALUATION = ["pe", "ps", "pb", "ev_ebitda"]
_C_QUALITY_HIGH = ["roe", "fcf_yield", "net_margin"]
_C_QUALITY_LOW = ["debt_to_equity"]


def _engine_c_sector(fundamentals: dict, ctx: dict, as_of) -> dict:
    dists = ctx["distributions"]
    val_parts, qual_parts = [], []
    used = {}

    for m in _C_VALUATION:
        v = fundamentals.get(m)
        if v is None or v <= 0 or m not in dists:
            continue
        p = _percentile(v, dists[m])
        if p is None:
            continue
        val_parts.append(1.0 - 2.0 * p)   # cheap -> +
        used[m] = round(p, 3)
    for m in _C_QUALITY_HIGH:
        v = fundamentals.get(m)
        if v is None or m not in dists:
            continue
        p = _percentile(v, dists[m])
        if p is None:
            continue
        qual_parts.append(2.0 * p - 1.0)  # high quality -> +
        used[m] = round(p, 3)
    for m in _C_QUALITY_LOW:
        v = fundamentals.get(m)
        if v is None or v < 0 or m not in dists:
            continue
        p = _percentile(v, dists[m])
        if p is None:
            continue
        qual_parts.append(1.0 - 2.0 * p)  # low leverage -> +
        used[m] = round(p, 3)

    val_sig = float(np.mean(val_parts)) if val_parts else None
    qual_sig = float(np.mean(qual_parts)) if qual_parts else None
    if val_sig is None and qual_sig is None:
        return {"signal": 0.0, "confidence": 0.0, "data_status": "MISSING",
                "details": {"reason": "no metrics overlap sector distributions",
                            "sector": ctx["sector"]}}
    signal = _clip(0.5 * (val_sig if val_sig is not None else qual_sig) +
                   0.5 * (qual_sig if qual_sig is not None else val_sig))
    n_used = len(val_parts) + len(qual_parts)
    status = ("CONFIRMED" if n_used >= 6 else
              "LIKELY" if n_used >= 4 else "UNCONFIRMED")
    return {
        "signal": signal,
        "confidence": round(min(n_used / 8.0, 1.0), 3),
        "data_status": status,
        "details": {
            "as_of": str(as_of),
            "mode": "sector_percentile",
            "sector": ctx["sector"],
            "sector_as_of": ctx.get("as_of"),
            "valuation_signal": round(val_sig, 4) if val_sig is not None else None,
            "quality_signal": round(qual_sig, 4) if qual_sig is not None else None,
            "percentiles": used,
            "reported_at": str(fundamentals.get("reported_at")),
            "note": ("valuation = cheap-vs-sector, quality = profitability/"
                     "leverage-vs-sector; loss-making P/E excluded"),
        },
    }


def _engine_c_hurdle(fundamentals: dict, as_of) -> dict:
    """Legacy hurdle-based scoring (kept for backward compatibility)."""
    parts, weights = [], []

    def add(cond_value, weight):
        parts.append(_clip(cond_value))
        weights.append(weight)

    pe = fundamentals.get("pe")
    if pe is not None and pe > 0:
        add(_clip((25.0 - pe) / 25.0), 0.25)          # cheaper PE -> positive
    fcfy = fundamentals.get("fcf_yield")
    if fcfy is not None:
        add(_clip((fcfy - 0.03) / 0.05), 0.25)        # FCF yield vs 3% hurdle
    roe = fundamentals.get("roe")
    if roe is not None:
        add(_clip((roe - 0.12) / 0.15), 0.25)         # ROE vs 12% hurdle
    dte = fundamentals.get("debt_to_equity")
    if dte is not None and dte >= 0:
        add(_clip((1.0 - dte) / 2.0), 0.15)           # lower leverage -> positive
    nm = fundamentals.get("net_margin")
    if nm is not None:
        add(_clip((nm - 0.10) / 0.20), 0.10)

    if not weights:
        return {"signal": 0.0, "confidence": 0.0, "data_status": "MISSING",
                "details": {"reason": "no usable fundamental fields"}}
    wsum = sum(weights)
    signal = _clip(sum(p * w for p, w in zip(parts, weights)) / wsum)
    n = len(weights)
    status = "CONFIRMED" if n >= 4 else ("LIKELY" if n >= 2 else "UNCONFIRMED")
    return {"signal": signal, "confidence": round(min(n / 5, 1.0), 3),
            "data_status": status,
            "details": {"as_of": str(as_of), "mode": "hurdle", "fields_used": n,
                        "reported_at": str(fundamentals.get("reported_at"))}}


def engine_c(fundamentals: dict | None, as_of,
             sector_context: dict | None = None,
             allow_undated: bool = False) -> dict:
    """Fundamental sub-signal.

    ``fundamentals``: point-in-time dict from
    ``data_connectors.edgar.compute_fundamentals`` (keys: pe, ps, pb,
    ev_ebitda, fcf_yield, gross_margin, net_margin, roe, roic,
    debt_to_equity, interest_coverage, reported_at). ``reported_at`` must
    be ``<= as_of`` or the record is rejected and the engine returns
    MISSING (point-in-time discipline).

    ``reported_at`` is REQUIRED: a record without it cannot prove its own
    knowability, so it is rejected as MISSING (reason "reported_at
    missing") instead of being scored. Pass ``allow_undated=True`` only
    for a caller that has established knowability out of band.

    ``sector_context``: from ``load_sector_context(sector)`` -- enables
    sector-percentile scoring (valuation + quality blend). Without it,
    falls back to hurdle scoring. Without usable fundamentals: MISSING,
    never fabricated.
    """
    as_of = pd.Timestamp(as_of)
    if not fundamentals:
        return {"signal": 0.0, "confidence": 0.0, "data_status": "MISSING",
                "details": {"reason": "no fundamentals supplied",
                            "note": STUB_REASON}}
    rep = fundamentals.get("reported_at")
    if rep is None:
        if not allow_undated:
            return {"signal": 0.0, "confidence": 0.0, "data_status": "MISSING",
                    "details": {"reason": "reported_at missing - cannot prove "
                                          "knowability at as_of %s, record "
                                          "rejected" % as_of,
                                "note": "pass allow_undated=True only when "
                                        "knowability is established out of "
                                        "band"}}
    elif pd.Timestamp(rep) > as_of:
        return {"signal": 0.0, "confidence": 0.0, "data_status": "MISSING",
                "details": {"reason": "reported_at %s is after as_of %s - "
                            "point-in-time violation, record rejected" % (rep, as_of)}}
    if sector_context:
        return _engine_c_sector(fundamentals, sector_context, as_of)
    return _engine_c_hurdle(fundamentals, as_of)


# ---------------------------------------------------------------------------
# Engine H — Cross-asset (correlations vs SPY/QQQ/VIX)
# ---------------------------------------------------------------------------
# Nominal relative-strength lookback (bars). Both legs are reduced together
# to the history actually available (see engine_h).
_H_REL_WINDOW = 63


def engine_h(df: pd.DataFrame, as_of,
             peers: dict[str, pd.DataFrame] | None = None) -> dict:
    """Cross-asset sub-signal.

    ``peers``: dict like {"SPY": df_spy, "QQQ": df_qqq, "VIX": df_vix},
    each an OHLCV DataFrame. Components:
      rel_strength : asset return minus SPY return over the SAME trailing
                     window (nominal 63d; both legs are shortened together
                     to the available common history and the window used is
                     reported as ``rel_strength_window_days``), tanh-scaled.
                     Needs >= 30 usable bars on both legs, else skipped.
      corr_regime  : return correlation with SPY over exactly that window;
                     very high correlation dampens idiosyncratic conviction
      vix_drag     : if VIX supplied, elevated VIX (vs its 63d median)
                     pushes the signal negative (risk-off)
    All joins are as-of aligned (peers truncated to <= as_of).
    """
    as_of = pd.Timestamp(as_of)
    d = df.loc[df.index <= as_of]
    if d.empty:
        raise ValueError("no bars at or before as_of")
    if not peers:
        return {"signal": 0.0, "confidence": 0.0, "data_status": "MISSING",
                "details": {"reason": "no peer series supplied",
                            "note": STUB_REASON}}
    rets = np.log(d["close"] / d["close"].shift(1)).dropna()
    parts, weights, notes = [], [], {}

    spy = peers.get("SPY")
    if spy is not None:
        s = spy.loc[spy.index <= as_of]
        srets = np.log(s["close"] / s["close"].shift(1)).dropna()
        common = rets.index.intersection(srets.index)
        # BOTH legs of the relative-strength read must cover the SAME
        # window. ``max(0, len(d) - 64)`` silently collapsed the asset to
        # its whole (short) history while SPY kept its full 63d leg, so a
        # 35-bar asset was compared on 34 days against SPY's 63 -- a
        # systematically optimistic, mislabelled number. Take the longest
        # window both series actually support and report it.
        win = min(len(common), _H_REL_WINDOW, len(d) - 1, len(s) - 1)
        if len(common) >= 30 and win >= 30:
            win_idx = common[-win:]
            corr = float(rets.loc[win_idx].corr(srets.loc[win_idx]))
            r_a = float(d["close"].iloc[-1] / d["close"].iloc[-(win + 1)] - 1.0)
            r_s = float(s["close"].iloc[-1] / s["close"].iloc[-(win + 1)] - 1.0)
            rel = _tanh_scale(r_a - r_s, scale=0.05)
            damp = 1.0 - 0.5 * min(max(corr, 0.0), 1.0)  # high corr -> less idiosyncratic edge
            parts.append(_clip(rel * damp))
            weights.append(0.6)
            notes.update({"spy_corr_63d": round(corr, 4),
                          "rel_strength_63d": round(r_a - r_s, 4),
                          # the window actually used: < 63 means the
                          # history was shorter than the nominal lookback
                          "rel_strength_window_days": int(win),
                          "asset_return_window": round(r_a, 4),
                          "spy_return_window": round(r_s, 4)})
        else:
            notes["rel_strength_skipped"] = (
                "insufficient common history (%d usable bars, need >= 30)"
                % max(win, 0))

    vix = peers.get("VIX")
    if vix is not None:
        v = vix.loc[vix.index <= as_of]["close"]
        if len(v) >= 63:
            med = float(v.tail(63).median())
            cur = float(v.iloc[-1])
            drag = _clip((med - cur) / 10.0)  # VIX above median -> negative
            parts.append(drag)
            weights.append(0.4)
            notes.update({"vix": round(cur, 2), "vix_median_63d": round(med, 2)})

    if not weights:
        return {"signal": 0.0, "confidence": 0.0, "data_status": "MISSING",
                "details": {"reason": "peers supplied but insufficient overlap"}}
    wsum = sum(weights)
    signal = _clip(sum(p * w for p, w in zip(parts, weights)) / wsum)
    return {"signal": signal, "confidence": round(wsum, 3),
            "data_status": "CONFIRMED" if wsum >= 1.0 else "LIKELY",
            "details": {"as_of": str(d.index[-1]), **notes}}


# ---------------------------------------------------------------------------
# Engine D — Macro (FRED)
# ---------------------------------------------------------------------------
# Macro feature keys (from data_connectors.fred.macro_features); these are
# the same keys the regime engine (E, later phase) will consume:
#   real_10y, curve_10y_2y, inflation_surprise, unrate_momentum,
#   hy_spread_z, dollar_trend, oil_trend
# Component reading: +1 = macro tailwind for equities, -1 = headwind.
_D_WEIGHTS = {
    "curve": 0.20,   # steep curve -> tailwind
    "real": 0.20,    # low real 10y yield -> tailwind
    "infl": 0.15,    # disinflation surprise -> tailwind
    "unemp": 0.15,   # falling unemployment -> tailwind
    "hy": 0.20,      # tight HY spreads -> tailwind
    "usd": 0.05,     # weak dollar -> mild tailwind (US multinationals)
    "oil": 0.05,     # falling oil -> mild tailwind (input costs)
}


def engine_d(macro: dict | None = None, as_of=None) -> dict:
    """Macro tailwind/headwind sub-signal from FRED features.

    ``macro``: dict from ``data_connectors.fred.macro_features``. Missing
    features are skipped and remaining weights renormalized; fewer than 3
    features -> MISSING (never imputed).
    """
    if not macro:
        return {"signal": 0.0, "confidence": 0.0, "data_status": "MISSING",
                "details": {"engine": "D",
                            "reason": "no macro features supplied",
                            "needs": "fred.macro_features(as_of)"}}
    parts, weights, comp = [], [], {}

    def add(name, value, weight):
        if value is not None and np.isfinite(value):
            parts.append(_clip(value))
            weights.append(weight)
            comp[name] = round(float(value), 4)

    f = macro
    add("curve", _tanh_scale(f["curve_10y_2y"], 1.0)
        if f.get("curve_10y_2y") is not None else None, _D_WEIGHTS["curve"])
    ry = f.get("real_10y")
    add("real", _tanh_scale((2.0 - ry) / 1.5, 1.0) if ry is not None else None,
        _D_WEIGHTS["real"])
    infl = f.get("inflation_surprise")
    add("infl", -_tanh_scale(infl, 0.5) if infl is not None else None,
        _D_WEIGHTS["infl"])
    un = f.get("unrate_momentum")
    add("unemp", -_tanh_scale(un, 0.5) if un is not None else None,
        _D_WEIGHTS["unemp"])
    hy = f.get("hy_spread_z")
    add("hy", -_tanh_scale(hy, 1.5) if hy is not None else None,
        _D_WEIGHTS["hy"])
    usd = f.get("dollar_trend")
    add("usd", -_tanh_scale(usd, 0.05) if usd is not None else None,
        _D_WEIGHTS["usd"])
    oil = f.get("oil_trend")
    add("oil", -_tanh_scale(oil, 0.10) if oil is not None else None,
        _D_WEIGHTS["oil"])

    n = len(parts)
    if n < 3:
        return {"signal": 0.0, "confidence": 0.0, "data_status": "MISSING",
                "details": {"engine": "D",
                            "reason": f"only {n} macro features available (< 3)",
                            "features_seen": list(f.keys())}}
    wsum = sum(weights)
    signal = _clip(sum(p * w for p, w in zip(parts, weights)) / wsum)
    status = ("CONFIRMED" if n == 7 else "LIKELY" if n >= 5 else "UNCONFIRMED")
    return {
        "signal": signal,
        "confidence": round(n / 7.0, 3),
        "data_status": status,
        "details": {
            "as_of": str(as_of) if as_of is not None else f.get("as_of"),
            "components": comp,
            "features_used": n,
            "obs_dates": f.get("obs_dates"),
            "note": ("tailwind/headwind blend; weights " +
                     ", ".join(f"{k}={v}" for k, v in _D_WEIGHTS.items())),
        },
    }


# ---------------------------------------------------------------------------
# Engine F — Options (delayed chain; never real-time)
# ---------------------------------------------------------------------------
# Weights are deliberately small: options positioning is a secondary,
# noisy signal. Documented here, not tuned.
_F_WEIGHTS = {
    "positioning": 0.25,  # put/call OI: heavy put OI (hedging) -> bearish read
    "iv": 0.20,           # extreme IV percentile -> contrarian caution
    "skew": 0.20,         # rich put skew (fear) -> bearish read
    "exp_move": 0.15,     # priced move rich vs HV20 -> elevated uncertainty
    "unusual": 0.10,      # net call/put unusual volume -> lean with the flow
    "term": 0.10,         # HV20 > HV60 (near-term fear) -> bearish read
}


def engine_f(options_data: dict | None = None, as_of=None) -> dict:
    """Options sub-signal from delayed chain features.

    ``options_data``: feature dict from
    ``data_connectors.options.chain_features`` (or {"features": {...}}).
    The chain date must be <= as_of or the snapshot is rejected.
    Result is ALWAYS data_status LIKELY with the note "delayed chain,
    not real-time" -- never present as real-time. No chain -> MISSING.
    """
    if isinstance(options_data, dict) and "features" in options_data \
            and "atm_iv" not in options_data:
        options_data = options_data["features"]
    if not options_data:
        return {"signal": 0.0, "confidence": 0.0, "data_status": "MISSING",
                "details": {"engine": "F",
                            "reason": "no options chain available",
                            "needs": "options.get_chain(ticker) -> chain_features"}}
    as_of = pd.Timestamp(as_of) if as_of is not None else None
    chain_date = options_data.get("chain_date")
    if as_of is not None and chain_date is not None:
        if pd.Timestamp(chain_date).date() > as_of.date():
            return {"signal": 0.0, "confidence": 0.0, "data_status": "MISSING",
                    "details": {"engine": "F",
                                "reason": f"chain_date {chain_date} after as_of "
                                          f"{as_of.date()} - snapshot rejected"}}
    f = options_data
    parts, weights, comp = [], [], {}

    def add(name, value, weight):
        if value is not None and np.isfinite(value):
            parts.append(_clip(value))
            weights.append(weight)
            comp[name] = round(float(value), 4)

    pc_oi = f.get("put_call_oi")
    add("positioning",
        -_tanh_scale((pc_oi - 0.9) / 0.4, 1.0) if pc_oi else None,
        _F_WEIGHTS["positioning"])
    ivp = f.get("iv_percentile")
    add("iv", -_tanh_scale((ivp - 0.75) / 0.15, 1.0)
        if ivp is not None else None, _F_WEIGHTS["iv"])
    skew = f.get("skew_proxy")
    add("skew", -_tanh_scale(skew * 8.0, 1.0) if skew is not None else None,
        _F_WEIGHTS["skew"])
    em, dte, hv20 = f.get("expected_move_pct"), f.get("dte_days"), f.get("hv20")
    em_read = None
    if em and dte and hv20:
        em_daily = (em / 100.0) / np.sqrt(dte)
        typical_daily = (hv20 / np.sqrt(252))
        em_read = -_tanh_scale((em_daily / typical_daily - 1.0) / 0.5, 1.0) \
            if typical_daily > 0 else None
    add("exp_move", em_read, _F_WEIGHTS["exp_move"])
    net_un = f.get("unusual_net_call")
    if f.get("unusual_count"):
        add("unusual", float(np.sign(net_un or 0)), _F_WEIGHTS["unusual"])
    hv60 = f.get("hv60")
    add("term", -_tanh_scale((hv20 - hv60) / 0.10, 1.0)
        if (hv20 and hv60) else None, _F_WEIGHTS["term"])

    if not parts:
        return {"signal": 0.0, "confidence": 0.0, "data_status": "MISSING",
                "details": {"engine": "F",
                            "reason": "chain present but no usable features"}}
    wsum = sum(weights)
    signal = _clip(sum(p * w for p, w in zip(parts, weights)) / wsum)
    return {
        "signal": signal,
        "confidence": round(max(wsum, 0.2), 3),
        "data_status": "LIKELY",
        "details": {
            "as_of": str(as_of.date()) if as_of is not None else None,
            "chain_date": chain_date,
            "components": comp,
            "features_used": len(parts),
            "note": ("delayed chain, not real-time; weights " +
                     ", ".join(f"{k}={v}" for k, v in _F_WEIGHTS.items())),
        },
    }


# ---------------------------------------------------------------------------
# Engine G — Sentiment (lexicon on 8-K + headlines)
# ---------------------------------------------------------------------------
def engine_g(sentiment_data: dict | None = None, as_of=None) -> dict:
    """Sentiment sub-signal from lexicon polarity.

    ``sentiment_data``: dict from
    ``data_connectors.sentiment.sentiment_summary`` with keys polarity,
    momentum_28d, velocity_z, n_docs. signal = 0.6*polarity +
    0.4*momentum_28d. No documents -> MISSING; < 5 documents caps
    confidence at 0.4.
    """
    if not sentiment_data or not sentiment_data.get("n_docs"):
        return {"signal": 0.0, "confidence": 0.0, "data_status": "MISSING",
                "details": {"engine": "G",
                            "reason": "no scored documents (no 8-K / headlines)",
                            "needs": "sentiment.sentiment_summary(cik, as_of)"}}
    pol = sentiment_data.get("polarity")
    mom = sentiment_data.get("momentum_28d") or 0.0
    if pol is None:
        return {"signal": 0.0, "confidence": 0.0, "data_status": "MISSING",
                "details": {"engine": "G", "reason": "polarity unavailable"}}
    n = int(sentiment_data["n_docs"])
    signal = _clip(0.6 * pol + 0.4 * mom)
    confidence = min(n / 10.0, 1.0)
    if n < 5:
        confidence = min(confidence, 0.4)  # thin coverage penalty
    status = "CONFIRMED" if n >= 10 else ("LIKELY" if n >= 5 else "UNCONFIRMED")
    return {
        "signal": signal,
        "confidence": round(confidence, 3),
        "data_status": status,
        "details": {
            "as_of": str(as_of) if as_of is not None
            else sentiment_data.get("as_of"),
            "polarity": round(pol, 4),
            "momentum_28d": round(float(mom), 4),
            "velocity_z": sentiment_data.get("velocity_z"),
            "n_docs": n,
            "note": "signal = 0.6*polarity + 0.4*momentum_28d; lexicon-scored",
        },
    }


# ---------------------------------------------------------------------------
# Documented stub: E (regime)
# ---------------------------------------------------------------------------
def _stub(engine: str, what: str) -> dict:
    return {"signal": 0.0, "confidence": 0.0, "data_status": "MISSING",
            "details": {"engine": engine, "reason": STUB_REASON,
                        "needs": what}}


def engine_e(*args, **kwargs) -> dict:
    """STUB - regime engine. Needs the market_regimes table / regime
    classifier (trend + volatility + breadth states). Returns MISSING."""
    return _stub("E", "regime classifier over market_regimes history")


def run_engines(df: pd.DataFrame, as_of, peers=None,
                fundamentals=None, macro_data=None, options_data=None,
                sentiment_data=None, sector_context=None) -> dict:
    """Run all engines A-H and return the per-engine result dicts.

    New Phase-4 inputs (all optional):
      fundamentals   point-in-time dict from edgar.compute_fundamentals
      sector_context from engines.load_sector_context(sector)
      macro_data     dict from fred.macro_features(as_of)
      options_data   feature dict from options.chain_features(chain, prices)
      sentiment_data dict from sentiment.sentiment_summary(cik, as_of)
    """
    return {
        "A": engine_a(df, as_of),
        "B": engine_b(df, as_of),
        "C": engine_c(fundamentals, as_of, sector_context),
        "D": engine_d(macro_data, as_of),
        "E": engine_e(),
        "F": engine_f(options_data, as_of),
        "G": engine_g(sentiment_data, as_of),
        "H": engine_h(df, as_of, peers),
    }
