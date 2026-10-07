"""Engine E — Market regime classifier (deterministic, rule-based).

Classifies the US equity market into one of eight regimes from
point-in-time inputs. Every threshold is a named module constant below;
no thresholds are invented inline. The classifier is fully deterministic:
same inputs -> same outputs, no RNG, no hidden state.

Inputs (each a point-in-time value at ``as_of``):
  trend     : SPY close relative to its 200-day SMA
  vix       : VIX index level (close)
  vix_chg20 : 20-trading-day percent change of the VIX
  curve     : 10Y minus 2Y Treasury yield spread (percentage points)
  hy_z      : high-yield credit-spread z-score vs trailing 1y (252d) window
  breadth   : % of the breadth-proxy universe with close > 50d SMA

Breadth proxy (documented): SPY plus the 11 GICS sector SPDR ETFs
(XLK XLF XLV XLI XLE XLU XLP XLY XLB XLC XLI -> deduped: XLK, XLF, XLV,
XLI, XLE, XLU, XLP, XLY, XLB, XLC, XLI). Equal-weighted "12 names".
This is a PROXY for true market breadth (which would need the full
S&P 500 constituent list); the proxy is cheaper to fetch and documented
here and in the registry notes. Callers that have true constituent
breadth may pass any universe — the classifier only needs the % number.

Missing inputs degrade gracefully: a rule whose required inputs are
missing simply does not fire (recorded in drivers as "input missing"),
and ``data_status`` reflects how many of the six inputs were present.
With <3 inputs present the classifier returns "Neutral" with confidence
30 rather than guessing from thin data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Regime set (ordered for tie-breaking stability)
# ---------------------------------------------------------------------------
REGIMES = ("Strong Bull", "Bull", "Neutral", "Risk-Off", "Bear",
           "Crisis", "High Vol", "Low Vol")

# ---------------------------------------------------------------------------
# Thresholds — every number below is THE definition of the classifier.
# Rationale is noted per constant; none may be changed without a registry
# version bump (they are part of model behavior).
# ---------------------------------------------------------------------------

# VIX level buckets (index close). VIX>30 has historically marked disorderly
# markets; VIX>40 has only printed in 2008, 2020-03, and flash events.
VIX_CALM = 15.0        # below: complacent / grind-up tape
VIX_NORMAL = 20.0      # below: ordinary bull-market chop
VIX_ELEVATED = 25.0    # above: stress building
VIX_HIGH = 30.0        # above: extreme fear (High Vol / Crisis territory)
VIX_CRISIS = 40.0      # at/above: crash regime

# VIX 20-trading-day percent change: a fast doubling of implied vol
# signals a shock even if the absolute level is not yet extreme.
VIX_SPIKE_CHG20 = 0.50

# SPY trend vs 200-day SMA (fraction). +/-5% bands are the classic
# long-term trend filter; -15% is a confirmed bear-market drawdown.
TREND_STRONG_UP = 0.05     # at/above: strong uptrend
TREND_DOWN = -0.05         # below: trend broken
TREND_BEAR = -0.15         # at/below: deep downtrend (bear)
TREND_DEEP_BEAR = -0.20    # at/below: crash-level drawdown

# 10Y-2Y curve (percentage points). Negative = inverted, the classic
# recession warning; <0.5 = flat/restrictive.
CURVE_INVERTED = 0.0
CURVE_FLAT = 0.5

# HY option-adjusted-spread z-score vs trailing 252d window.
# >2 = credit stress, >3 = severe (2008 / 2020-03 printed 4-6).
HY_Z_STRESS = 2.0
HY_Z_SEVERE = 3.0
HY_Z_ELEVATED = 1.5

# Breadth: % of proxy universe above its 50-day SMA.
BREADTH_STRONG = 60.0
BREADTH_HEALTHY = 50.0
BREADTH_NEUTRAL_LO = 40.0
BREADTH_WEAK = 30.0
BREADTH_CAPITULATION = 20.0

# Confidence: base + per-driver increment, capped. A driver is one fired
# threshold condition recorded in the output drivers list.
CONF_BASE = 30.0
CONF_PER_DRIVER = 15.0
CONF_MAX = 100.0
CONF_MIN_INPUTS = 3        # fewer inputs -> Neutral @ 30, not a guess

# Warmup bars before regime_history() emits rows: 200 for the SMA200,
# 252 for a fully-formed HY z-score window (early rows use min_periods).
WARMUP_BARS = 252
HY_Z_MIN_PERIODS = 60

# Breadth proxy constituents (documented proxy, see module docstring).
BREADTH_PROXY = ("SPY", "XLK", "XLF", "XLV", "XLI", "XLE", "XLU",
                 "XLP", "XLY", "XLB", "XLC")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _snap(series: pd.Series, as_of) -> float | None:
    """Last value at or before ``as_of`` (point-in-time)."""
    as_of = pd.Timestamp(as_of)
    s = series.loc[series.index <= as_of].dropna()
    if s.empty:
        return None
    v = float(s.iloc[-1])
    return v if np.isfinite(v) else None


def _status(n_present: int) -> str:
    if n_present >= 6:
        return "CONFIRMED"
    if n_present == 5:
        return "LIKELY"
    if n_present >= CONF_MIN_INPUTS:
        return "UNCONFIRMED"
    return "MISSING"


# ---------------------------------------------------------------------------
# Core classifier
# ---------------------------------------------------------------------------
def classify_regime(as_of, trend=None, vix=None, vix_chg20=None,
                    curve=None, hy_z=None, breadth=None) -> dict:
    """Classify the market regime at ``as_of``.

    Each input may be a float (already snapped) or a pandas Series indexed
    by date (snapped to ``as_of`` internally). ``None`` = input missing.

    Returns {"regime", "confidence" (0-100), "drivers" (list of str),
             "data_status", "inputs" (echo of snapped values), "as_of"}.
    """
    def val(x):
        if x is None:
            return None
        if isinstance(x, pd.Series):
            return _snap(x, as_of)
        v = float(x)
        return v if np.isfinite(v) else None

    inp = {"trend": val(trend), "vix": val(vix), "vix_chg20": val(vix_chg20),
           "curve": val(curve), "hy_z": val(hy_z), "breadth": val(breadth)}
    n_present = sum(1 for v in inp.values() if v is not None)
    status = _status(n_present)

    if n_present < CONF_MIN_INPUTS:
        return {"regime": "Neutral", "confidence": CONF_BASE,
                "drivers": ["insufficient inputs (%d/6) - refusing to guess"
                            % n_present],
                "data_status": status, "inputs": inp,
                "as_of": str(pd.Timestamp(as_of).date())}

    t, vx = inp["trend"], inp["vix"]
    hz, cv, bth, vxc = inp["hy_z"], inp["curve"], inp["breadth"], inp["vix_chg20"]

    # Every check returns True (fired) / False (not fired) / None (input
    # missing). Checks are evaluated in priority order; the FIRST fired
    # check decides the regime, but ALL fired checks are recorded as
    # drivers (they are facts about the tape, not contradictory claims).
    checks: list[tuple[str, bool | None, str]] = [
        ("Crisis",
         None if vx is None else vx >= VIX_CRISIS,
         f"VIX {vx:.1f} (>= {VIX_CRISIS:g})" if vx is not None else "VIX"),
        ("Crisis",
         None if (t is None or vx is None)
         else (t <= TREND_DEEP_BEAR and vx >= VIX_HIGH),
         "SPY deep drawdown with extreme VIX"
         if t is None or vx is None
         else f"SPY {t*100:.1f}% vs 200d SMA with VIX {vx:.1f}"),
        ("High Vol",
         None if vx is None else vx >= VIX_HIGH,
         f"VIX {vx:.1f} (>= {VIX_HIGH:g}), trend not crash-level"
         if vx is not None else "VIX"),
        ("Bear",
         None if t is None else t <= TREND_BEAR,
         f"SPY {t*100:.1f}% vs 200d SMA (<= {TREND_BEAR*100:g}%)"
         if t is not None else "trend"),
        ("Bear",
         None if (hz is None or vx is None)
         else (hz >= HY_Z_SEVERE and vx >= VIX_ELEVATED),
         "credit-led stress"
         if hz is None or vx is None
         else f"HY z {hz:.1f} (severe) + VIX {vx:.1f}"),
        ("Risk-Off",
         None if (cv is None or hz is None)
         else (cv < CURVE_INVERTED and hz >= HY_Z_ELEVATED),
         "curve/credit"
         if cv is None or hz is None
         else f"curve inverted ({cv:.2f}) + HY z {hz:.1f}"),
        ("Risk-Off",
         None if (vx is None or t is None)
         else (vx >= VIX_ELEVATED and t <= TREND_DOWN),
         "vol+trend"
         if vx is None or t is None
         else f"VIX {vx:.1f} with SPY below 200d SMA"),
        ("Risk-Off",
         None if vxc is None else vxc >= VIX_SPIKE_CHG20,
         f"VIX +{vxc*100:.0f}% in 20d (vol shock)"
         if vxc is not None else "VIX spike"),
        ("Low Vol",
         None if (vx is None or t is None or bth is None)
         else (vx < VIX_CALM and abs(t) <= 0.03
               and BREADTH_NEUTRAL_LO <= bth <= BREADTH_STRONG),
         "calm grind"
         if vx is None or t is None or bth is None
         else f"VIX {vx:.1f} (< {VIX_CALM:g}), trend flat, breadth mid-range"),
        # NOTE: the bull-side rules do NOT hard-gate on the HY z-score.
        # When hy_z is missing the core (trend+breadth+vol) still decides;
        # the missing credit confirmation is recorded as a driver note
        # below and penalized via the missing-input confidence haircut.
        # (Rationale: macro feeds are optional/degraded in this
        # deployment; a bull market must remain classifiable without them.)
        ("Strong Bull",
         None if (t is None or vx is None or bth is None)
         else (t >= TREND_STRONG_UP and vx < VIX_NORMAL
               and bth >= BREADTH_STRONG
               and (hz is None or hz < HY_Z_ELEVATED)),
         "trend+breadth+calm vol (credit n/a)"
         if hz is None and t is not None and vx is not None
         and bth is not None
         else "trend+breadth+calm vol+calm credit (all four green)"),
        ("Bull",
         None if (t is None or bth is None or vx is None)
         else (t >= 0.0 and bth >= BREADTH_HEALTHY
               and vx < VIX_ELEVATED
               and (hz is None or hz < HY_Z_STRESS)),
         "above 200d SMA, breadth healthy, vol contained (credit n/a)"
         if hz is None and t is not None and bth is not None
         and vx is not None
         else "above 200d SMA, breadth healthy, vol/credit contained"),
    ]

    drivers: list[str] = []
    fired: list[str] = []
    for reg, ok, text in checks:
        if ok is None:
            drivers.append(text + " | input missing")
        elif ok:
            drivers.append(text)
            fired.append(reg)

    regime = fired[0] if fired else "Neutral"
    if not fired:
        drivers.append("no rule fired - mixed/neutral tape")
    if hz is None and regime in ("Bull", "Strong Bull"):
        drivers.append("HY spread z-score | input missing "
                       "(bull call made without credit confirmation)")

    n_drivers = len(fired) if fired else 0
    confidence = min(CONF_MAX, CONF_BASE + CONF_PER_DRIVER * max(n_drivers, 1))
    if n_present < 6:
        # penalize 5 points per missing input (documented)
        confidence = max(CONF_BASE, confidence - 5.0 * (6 - n_present))

    return {"regime": regime, "confidence": round(float(confidence), 1),
            "drivers": drivers, "data_status": status, "inputs": inp,
            "as_of": str(pd.Timestamp(as_of).date())}


# ---------------------------------------------------------------------------
# History builder — one row per trading day, matches the market_regimes
# table shape: {time, regime, confidence, drivers}.
# ---------------------------------------------------------------------------
def regime_history(prices: dict[str, pd.DataFrame],
                   macro: pd.DataFrame | None = None) -> list[dict]:
    """Build daily regime history from point-in-time inputs.

    ``prices``: {"SPY": OHLCV df, "VIX": OHLCV df (close = index level),
    ...breadth-proxy ETFs...}. ``macro``: DataFrame indexed by date with
    columns "curve_10y2y" and "hy_spread" (spread LEVEL; the z-score vs
    the trailing 252d window with min_periods=60 is computed causally
    here). All series are truncated to <= each day internally — the
    200d SMA, 20d VIX change, 50d-SMA breadth and HY z-score are trailing
    (causal) by construction, so no lookahead.

    Rows start after ``WARMUP_BARS`` (252) SPY bars so the 200d SMA and
    the HY z window are formed; early HY z uses min_periods=60.
    """
    spy = prices["SPY"].sort_index()
    vix = prices["VIX"].sort_index()
    idx = spy.index[WARMUP_BARS:]
    out: list[dict] = []

    spy_close = spy["close"]
    vix_close = vix["close"]
    sma200 = spy_close.rolling(200).mean()
    vix_chg20 = vix_close.pct_change(20)

    # breadth proxy: % of constituents above their 50d SMA (causal)
    members = [m for m in BREADTH_PROXY if m in prices]
    above = []
    for m in members:
        c = prices[m].sort_index()["close"]
        above.append((c > c.rolling(50).mean()).astype(float))
    breadth = (pd.concat(above, axis=1).mean(axis=1) * 100.0
               if above else pd.Series(np.nan, index=spy.index))

    if macro is not None and not macro.empty:
        m = macro.sort_index()
        curve = m.get("curve_10y2y")
        spread = m.get("hy_spread")
        hy_z = None
        if spread is not None:
            mu = spread.rolling(252, min_periods=HY_Z_MIN_PERIODS).mean()
            sd = spread.rolling(252, min_periods=HY_Z_MIN_PERIODS).std(ddof=1)
            hy_z = (spread - mu) / sd.replace(0.0, np.nan)
    else:
        curve, hy_z = None, None

    for day in idx:
        trend = (float(spy_close.loc[day] / sma200.loc[day] - 1.0)
                 if pd.notna(sma200.loc[day]) else None)
        r = classify_regime(
            day, trend=trend, vix=vix_close, vix_chg20=vix_chg20,
            curve=curve, hy_z=hy_z, breadth=breadth)
        out.append({"time": pd.Timestamp(day).strftime("%Y-%m-%d"),
                    "regime": r["regime"], "confidence": r["confidence"],
                    "drivers": r["drivers"]})
    return out


def history_to_frame(rows: list[dict]) -> pd.DataFrame:
    """Convenience: regime history rows -> DataFrame indexed by time."""
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time").sort_index()
