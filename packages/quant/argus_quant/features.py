"""Point-in-time feature builder.

ANTI-LEAKAGE CONTRACT (locked law):
    ``build_features(df, as_of)`` truncates ``df`` to bars with
    ``index <= as_of`` BEFORE any computation. No feature may reference a
    timestamp later than ``as_of``. ``as_of`` is snapped to the last bar
    ``<= as_of`` and the snapped value is returned.

Every feature is registered in ``FEATURE_SPECS`` with its lookback in bars,
so the full provenance of a feature vector is auditable. ``feature_hash``
is the sha256 of a canonical JSON encoding of the feature values
(sorted names, rounded floats) - identical inputs always produce the
identical hash.

REPRODUCIBILITY CONTRACT (locked): the vector is computed on at most the
last ``MIN_HISTORY_BARS`` bars (the longest registered lookback). Without
that bound, recursively-seeded indicators (RSI/MACD/ATR/ADX) and even
pandas' rolling accumulators make the values - and therefore the hash -
depend on how much older history the caller happened to pass in, so the
same ``as_of`` produced different hashes for different windows. With the
bound, any caller supplying at least ``MIN_HISTORY_BARS`` bars before
``as_of`` gets the identical vector and the identical hash.
"""

from __future__ import annotations

import hashlib
import json
import logging

import numpy as np
import pandas as pd

from . import indicators as ind

log = logging.getLogger("argus_quant.features")


def _last(s: pd.Series) -> float:
    v = s.iloc[-1]
    return float(v) if pd.notna(v) else float("nan")


def _f_rsi_14(d): return _last(ind.rsi(d["close"], 14))
def _f_macd_hist(d): return _last(ind.macd(d["close"])["macd_hist"])
def _f_stoch_k(d):
    return _last(ind.stochastic(d["high"], d["low"], d["close"])["stoch_k"])
def _f_stoch_d(d):
    return _last(ind.stochastic(d["high"], d["low"], d["close"])["stoch_d"])
def _f_adx_14(d): return _last(ind.adx(d["high"], d["low"], d["close"])["adx"])
def _f_atr_pct(d):
    a = _last(ind.atr(d["high"], d["low"], d["close"], 14))
    c = _last(d["close"])
    return a / c if c else float("nan")
def _f_bb_pctb(d): return _last(ind.bollinger(d["close"])["bb_pctb"])
def _f_bb_bw(d): return _last(ind.bollinger(d["close"])["bb_bandwidth"])
def _f_kc_pos(d):
    kc = ind.keltner(d["high"], d["low"], d["close"])
    up, lo, c = _last(kc["kc_upper"]), _last(kc["kc_lower"]), _last(d["close"])
    w = up - lo
    return (c - lo) / w if w else float("nan")
def _f_ichi_cloud(d):
    ichi = ind.ichimoku(d["high"], d["low"], d["close"])
    a = _last(ichi["ichimoku_senkou_a"])
    b = _last(ichi["ichimoku_senkou_b"])
    c = _last(d["close"])
    if pd.isna(a) or pd.isna(b):
        return float("nan")
    top, bot = max(a, b), min(a, b)
    return 1.0 if c > top else (-1.0 if c < bot else 0.0)
def _f_close_sma50(d):
    s = ind.sma(d["close"], 50)
    c, m = _last(d["close"]), _last(s)
    return (c / m - 1.0) if m else float("nan")
def _f_sma_cross(d):
    f, s = _last(ind.sma(d["close"], 20)), _last(ind.sma(d["close"], 50))
    return (f / s - 1.0) if s else float("nan")
_VWAP_WINDOW = 20   # fixed trailing window (bars) for the VWAP distance
_OBV_WINDOW = 21    # fixed trailing window (bars) for the OBV slope


def _f_vwap_dist(d):
    """Close / 20-bar VWAP - 1.

    WINDOW-INDEPENDENCE: computed on a FIXED trailing window (_VWAP_WINDOW
    bars), not on the anchored cumulative VWAP from the start of the
    supplied history. The same ``as_of`` therefore yields the same value
    no matter how much older history the caller passed in."""
    if len(d) < _VWAP_WINDOW:
        return float("nan")
    w = d.tail(_VWAP_WINDOW)
    typical = (w["high"] + w["low"] + w["close"]) / 3.0
    vol = w["volume"].astype(float)
    total = float(vol.sum())
    if total <= 0:
        return float("nan")
    vwap = float((typical * vol).sum() / total)
    c = _last(d["close"])
    return (c / vwap - 1.0) if vwap else float("nan")


def _f_obv_slope(d):
    """20-bar OBV slope, normalized by the window's traded volume.

    WINDOW-INDEPENDENCE: the OBV accumulation is anchored at the FIRST bar
    of a fixed trailing window (_OBV_WINDOW bars) and the slope is scaled
    by that window's total volume, so the value depends only on those bars
    and not on where the supplied history starts."""
    if len(d) < _OBV_WINDOW:
        return float("nan")
    w = d.tail(_OBV_WINDOW)
    direction = np.sign(w["close"].diff().fillna(0.0))
    o = (direction * w["volume"].astype(float)).cumsum().to_numpy(dtype=float)
    x = np.arange(float(_OBV_WINDOW))
    slope = float(np.polyfit(x, o, 1)[0])
    scale = float(w["volume"].astype(float).abs().sum()) + 1e-9
    return slope / scale * 252.0
def _f_volume_z(d):
    v = d["volume"].tail(21)
    if len(v) < 21:
        return float("nan")
    mu, sd = v.iloc[:-1].mean(), v.iloc[:-1].std(ddof=1)
    return float((v.iloc[-1] - mu) / sd) if sd else float("nan")
def _ret(d, n):
    c = d["close"]
    if len(c) < n + 1:
        return float("nan")
    return float(c.iloc[-1] / c.iloc[-(n + 1)] - 1.0)
def _f_mom_20(d): return _ret(d, 20)
def _f_mom_63(d): return _ret(d, 63)
def _f_mom_126(d): return _ret(d, 126)
def _f_mom_252(d): return _ret(d, 252)
def _f_reversal_1m(d):
    r = _ret(d, 21)
    return -r if pd.notna(r) else float("nan")
def _f_rvol_20(d): return _last(ind.realized_volatility(d["close"], 20))
def _f_rvol_63(d): return _last(ind.realized_volatility(d["close"], 63))
def _f_rvol_ratio(d):
    a, b = _f_rvol_20(d), _f_rvol_63(d)
    return a / b if b else float("nan")
def _f_high_52w(d):
    c = d["close"]
    if len(c) < 252:
        return float("nan")
    return float(c.iloc[-1] / c.tail(252).max() - 1.0)


# ---------------------------------------------------------------------------
# Feature registry: every feature documents its lookback in bars.
# Every feature is now bounded by a finite trailing lookback, so the same
# ``as_of`` produces the same vector (and the same feature_hash) for any
# history window with at least that many bars.
# engine: "A" technical, "B" factor, "H" cross-asset.
# ---------------------------------------------------------------------------
FEATURE_SPECS = [
    {"name": "rsi_14", "engine": "A", "lookback_bars": 60,
     "inputs": ["close"],
     "description": "Wilder RSI(14). Oversold <30, overbought >70.",
     "compute": _f_rsi_14},
    {"name": "macd_hist", "engine": "A", "lookback_bars": 60,
     "inputs": ["close"],
     "description": "MACD(12,26,9) histogram; >0 bullish momentum.",
     "compute": _f_macd_hist},
    {"name": "stoch_k", "engine": "A", "lookback_bars": 30,
     "inputs": ["high", "low", "close"],
     "description": "Stochastic %K(14).",
     "compute": _f_stoch_k},
    {"name": "stoch_d", "engine": "A", "lookback_bars": 30,
     "inputs": ["high", "low", "close"],
     "description": "Stochastic %D(14,3).",
     "compute": _f_stoch_d},
    {"name": "adx_14", "engine": "A", "lookback_bars": 60,
     "inputs": ["high", "low", "close"],
     "description": "ADX(14) trend strength; >25 = trending.",
     "compute": _f_adx_14},
    {"name": "atr_pct", "engine": "A", "lookback_bars": 30,
     "inputs": ["high", "low", "close"],
     "description": "ATR(14)/close - normalized volatility.",
     "compute": _f_atr_pct},
    {"name": "bb_pctb", "engine": "A", "lookback_bars": 40,
     "inputs": ["close"],
     "description": "Bollinger %B(20,2); >1 above band, <0 below.",
     "compute": _f_bb_pctb},
    {"name": "bb_bandwidth", "engine": "A", "lookback_bars": 40,
     "inputs": ["close"],
     "description": "Bollinger bandwidth(20,2) - squeeze/expansion.",
     "compute": _f_bb_bw},
    {"name": "kc_position", "engine": "A", "lookback_bars": 40,
     "inputs": ["high", "low", "close"],
     "description": "Close position inside Keltner(20,10,2); 0-1 scale.",
     "compute": _f_kc_pos},
    {"name": "ichimoku_cloud", "engine": "A", "lookback_bars": 78,
     "inputs": ["high", "low", "close"],
     "description": "Close vs cloud: +1 above, 0 inside, -1 below. "
                    "Cloud edges at t were known since t-26.",
     "compute": _f_ichi_cloud},
    {"name": "close_vs_sma50", "engine": "A", "lookback_bars": 60,
     "inputs": ["close"],
     "description": "Close/SMA(50)-1 - distance from medium trend.",
     "compute": _f_close_sma50},
    {"name": "sma20_sma50_cross", "engine": "A", "lookback_bars": 60,
     "inputs": ["close"],
     "description": "SMA(20)/SMA(50)-1 - golden/death cross distance.",
     "compute": _f_sma_cross},
    {"name": "vwap_distance", "engine": "A", "lookback_bars": 20,
     "inputs": ["high", "low", "close", "volume"],
     "description": "Close/20-bar VWAP-1. Fixed trailing window (not the "
                    "anchored full-history VWAP), so the value and the "
                    "feature_hash do not depend on where the history starts.",
     "compute": _f_vwap_dist},
    {"name": "obv_slope_20", "engine": "A", "lookback_bars": 21,
     "inputs": ["close", "volume"],
     "description": "20-bar OBV slope anchored to a fixed 21-bar window and "
                    "normalized by that window's volume - window-independent.",
     "compute": _f_obv_slope},
    {"name": "volume_zscore_20", "engine": "A", "lookback_bars": 21,
     "inputs": ["volume"],
     "description": "Current bar volume z-score vs trailing 20 bars.",
     "compute": _f_volume_z},
    {"name": "momentum_20d", "engine": "B", "lookback_bars": 21,
     "inputs": ["close"],
     "description": "20-day total return.",
     "compute": _f_mom_20},
    {"name": "momentum_63d", "engine": "B", "lookback_bars": 64,
     "inputs": ["close"],
     "description": "63-day total return (~3 months).",
     "compute": _f_mom_63},
    {"name": "momentum_126d", "engine": "B", "lookback_bars": 127,
     "inputs": ["close"],
     "description": "126-day total return (~6 months).",
     "compute": _f_mom_126},
    {"name": "momentum_252d", "engine": "B", "lookback_bars": 253,
     "inputs": ["close"],
     "description": "252-day total return (~12 months).",
     "compute": _f_mom_252},
    {"name": "reversal_1m", "engine": "B", "lookback_bars": 22,
     "inputs": ["close"],
     "description": "Negated 21-day return - short-term reversal factor.",
     "compute": _f_reversal_1m},
    {"name": "realized_vol_20d", "engine": "B", "lookback_bars": 21,
     "inputs": ["close"],
     "description": "Annualized realized vol, trailing 20 bars.",
     "compute": _f_rvol_20},
    {"name": "realized_vol_63d", "engine": "B", "lookback_bars": 64,
     "inputs": ["close"],
     "description": "Annualized realized vol, trailing 63 bars.",
     "compute": _f_rvol_63},
    {"name": "vol_ratio_20_63", "engine": "B", "lookback_bars": 64,
     "inputs": ["close"],
     "description": "vol20/vol63 - volatility expansion (>1) or contraction.",
     "compute": _f_rvol_ratio},
    {"name": "dist_52w_high", "engine": "B", "lookback_bars": 252,
     "inputs": ["close"],
     "description": "Close/252-bar high - 1 - distance from 52-week high.",
     "compute": _f_high_52w},
]

SPEC_BY_NAME = {s["name"]: s for s in FEATURE_SPECS}

# Longest registered lookback (momentum_252d -> 253 bars). The feature
# vector is computed on at most this many trailing bars so that the same
# ``as_of`` always yields the same values and the same feature_hash,
# regardless of how much older history the caller passed in.
MIN_HISTORY_BARS = max(int(s["lookback_bars"] or 0) for s in FEATURE_SPECS)
assert MIN_HISTORY_BARS == 253, "MIN_HISTORY_BARS must cover every lookback"


def _canonical_json(values: dict) -> str:
    """Sorted names, NaN -> null, floats rounded to 10dp."""
    clean = {}
    for k in sorted(values):
        v = values[k]
        clean[k] = (None if (v is None or (isinstance(v, float) and np.isnan(v)))
                    else round(float(v), 10))
    return json.dumps(clean, sort_keys=True, separators=(",", ":"))


def build_features(df: pd.DataFrame, as_of) -> tuple:
    """Build the point-in-time feature vector for the last bar ``<= as_of``.

    Returns ``(features, feature_hash, as_of_snapped)``:
      features: one-row DataFrame indexed by the snapped as-of timestamp,
        one column per registered feature name. Its ``attrs`` record how
        many bars were used (``history_bars_used``) and the bound
        (``min_history_bars``).
      feature_hash: sha256 of the canonical feature JSON.
      as_of_snapped: the actual bar timestamp used (last bar <= as_of).

    Only bars ``<= as_of`` are ever read, and at most the last
    ``MIN_HISTORY_BARS`` of them, so the same ``as_of`` with at least that
    much history always produces the same vector and hash.

    Raises ValueError if no bar exists ``<= as_of`` or the index is not a
    DatetimeIndex.
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("df must have a DatetimeIndex")
    as_of = pd.Timestamp(as_of)
    if as_of.tz is None and df.index.tz is not None:
        as_of = as_of.tz_localize(df.index.tz)
    truncated = df.loc[df.index <= as_of]
    if truncated.empty:
        raise ValueError("no bars at or before as_of=%s" % as_of)
    snapped = truncated.index[-1]
    # Bound the history to the longest registered lookback: same as_of and
    # >= MIN_HISTORY_BARS of history -> identical values and identical hash.
    history = truncated.tail(MIN_HISTORY_BARS)

    values = {}
    errors: dict[str, str] = {}
    for spec in FEATURE_SPECS:
        try:
            values[spec["name"]] = float(spec["compute"](history))
        except (ValueError, KeyError, TypeError, ZeroDivisionError,
                IndexError) as exc:
            # A missing column / bad dtype / short window is an EXPECTED
            # degradation -> NaN, but it is logged and reported: with B-1's
            # old zero-fill this silent NaN was turned into a bullish
            # component, so the producer's silence fed the consumer's
            # optimism. ``values_error`` travels with the vector (and into
            # the attrs) so a NaN can be explained.
            errors[spec["name"]] = f"{type(exc).__name__}: {exc}"
            values[spec["name"]] = float("nan")
            log.warning("feature %s failed on %d bars: %s: %s",
                        spec["name"], len(history), type(exc).__name__, exc)
        except Exception as exc:  # noqa: BLE001 - never break the vector
            errors[spec["name"]] = f"UNEXPECTED {type(exc).__name__}: {exc}"
            values[spec["name"]] = float("nan")
            log.error("feature %s raised an UNEXPECTED %s: %s (reported as "
                      "NaN and flagged in values_error)", spec["name"],
                      type(exc).__name__, exc, exc_info=True)

    features = pd.DataFrame([values],
                            index=pd.DatetimeIndex([snapped], name="as_of"))
    features.attrs["history_bars_used"] = int(len(history))
    features.attrs["min_history_bars"] = int(MIN_HISTORY_BARS)
    features.attrs["values_error"] = dict(errors)
    feature_hash = hashlib.sha256(
        _canonical_json(values).encode("utf-8")).hexdigest()
    return features, feature_hash, snapped
