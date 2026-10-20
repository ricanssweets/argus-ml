"""Technical indicators — pure, deterministic, strictly causal.

ANTI-LEAKAGE CONTRACT (locked law):
    Every value at bar ``t`` is computed from bars ``<= t`` ONLY.
    All rolling statistics use trailing windows ending at ``t``.
    ``min_periods`` is set explicitly so warmup periods are NaN, never
    forward-filled or silently estimated.

Shift conventions (documented per function):
    * Trailing windows: window ``n`` at bar ``t`` covers bars ``[t-n+1, t]``.
    * Ichimoku Senkou spans are shifted **forward** (+26) as a *display*
      convention: the value shown at bar ``t`` was fully determined by data
      ``<= t-26`` (already known at ``t-26``). No future data is used.
    * Ichimoku Chikou is shifted **backward** (-26) as a display convention:
      the value shown at bar ``t`` is ``close[t+26]``... which WOULD be a
      leak. Therefore the Chikou series is returned **unshifted** by default
      (``close[t]``) and the -26 display shift is left to the caller, with
      the leakage warning in the docstring.
    * VWAP: intraday input -> per-session VWAP (resets each calendar day);
      daily input -> anchored cumulative VWAP from the first bar (documented
      fallback, NOT a true session VWAP).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

OHLCV_COLS = ("open", "high", "low", "close", "volume")


def _check_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in OHLCV_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"OHLCV DataFrame missing columns: {missing}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("OHLCV DataFrame must have a DatetimeIndex")
    return df


def sma(close: pd.Series, n: int) -> pd.Series:
    """Simple moving average. Bar t uses closes [t-n+1, t]. NaN before n bars."""
    return close.rolling(window=n, min_periods=n).mean()


def ema(close: pd.Series, n: int) -> pd.Series:
    """Exponential moving average (recursive, adjust=False).

    Bar t uses only closes <= t (recursive weighting, seeded from close[0]).
    min_periods=n -> NaN warmup of n bars.
    """
    return close.ewm(span=n, adjust=False, min_periods=n).mean()


def _wilder_mean(s: pd.Series, n: int) -> pd.Series:
    """Wilder's average: first value = simple mean of the first n values,
    then recursive ``(prev*(n-1) + x)/n``. Strictly causal."""
    simple = s.rolling(window=n, min_periods=n).mean()
    arr = simple.to_numpy(dtype=float).copy()
    sv = s.to_numpy(dtype=float)
    valid = np.flatnonzero(~np.isnan(arr))
    if len(valid):
        prev = arr[valid[0]]
        for i in range(valid[0] + 1, len(arr)):
            if np.isnan(sv[i]):
                arr[i] = np.nan
            else:
                prev = (prev * (n - 1) + sv[i]) / n
                arr[i] = prev
    return pd.Series(arr, index=s.index)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """RSI with Wilder's smoothing (exact Wilder seeding).

    First average = simple mean of the first n gains/losses, then the
    recursive Wilder update ``(prev*(n-1)+x)/n`` — both reference only
    bars <= t. NaN warmup of n bars (first valid at 0-based index n,
    because diff() leaves bar 0 as NaN).

    Textbook check: for the classic 20-price example series, RSI(14) at the
    15th bar equals 100 - 100/(1 + mean(gains)/mean(losses)) computed from
    the first 14 changes (see tests/test_indicators.py).
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = _wilder_mean(gain, n)
    avg_loss = _wilder_mean(loss, n)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # Edge cases, in this order:
    #   no losses at all   -> 100 (all gains)
    #   no gains at all    -> 0   (all losses)
    #   neither (flat)     -> 50  (neutral: no direction to measure)
    # The flat case used to fall through to the "no gains" branch and print
    # 0.0 -- an "extreme oversold" reading fabricated from a constant price
    # (engine A then scored (50-0)/50 = +1.0, a maximal BUY).
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(avg_gain != 0.0, 0.0)
    out = out.where(~((avg_gain == 0.0) & (avg_loss == 0.0)), 50.0)
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    """MACD line, signal line, histogram. All EMAs are trailing (bar t uses
    closes <= t). Warmup: NaN until the slow EMA has min_periods bars."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False,
                                min_periods=signal).mean()
    return pd.DataFrame({
        "macd": macd_line,
        "macd_signal": signal_line,
        "macd_hist": macd_line - signal_line,
    }, index=close.index)


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
               k: int = 14, d: int = 3) -> pd.DataFrame:
    """Stochastic %K/%D. Bar t uses highs/lows/closes [t-k+1, t].
    %K is NaN when highest high == lowest low (flat window)."""
    ll = low.rolling(window=k, min_periods=k).min()
    hh = high.rolling(window=k, min_periods=k).max()
    denom = (hh - ll).replace(0.0, np.nan)
    pct_k = 100.0 * (close - ll) / denom
    pct_d = pct_k.rolling(window=d, min_periods=d).mean()
    return pd.DataFrame({"stoch_k": pct_k, "stoch_d": pct_d}, index=close.index)


def _true_range(high: pd.Series, low: pd.Series,
               close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        n: int = 14) -> pd.Series:
    """Average True Range with Wilder's smoothing (seed = simple mean).

    Bar 0 has no previous close, so its reduced true range (high-low only)
    is excluded from the seed; the first average is the simple mean of the
    next ``n`` complete true ranges and the recursion then applies
    ``(prev*(n-1) + tr)/n`` -- the same seeding ``rsi`` uses. First valid
    value at 0-based index ``n`` (n+1 bars of warmup). TR at bar t uses
    high/low[t] and close[t-1] -- no future data.
    """
    tr = _true_range(high, low, close).copy()
    if len(tr):
        tr.iloc[0] = np.nan  # no previous close -> not a complete TR
    return _wilder_mean(tr, n)


def _directional_movement(high: pd.Series, low: pd.Series):
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    # Bar 0 has no previous high/low: the first directional comparison is
    # undefined, so it is excluded rather than fabricated as 0.0.
    if len(plus_dm):
        plus_dm.iloc[0] = np.nan
        minus_dm.iloc[0] = np.nan
    return plus_dm, minus_dm


def adx(high: pd.Series, low: pd.Series, close: pd.Series,
        n: int = 14) -> pd.DataFrame:
    """ADX / +DI / -DI with Wilder's smoothing (seed = simple mean, as in
    ``rsi``/``atr``). Bar t uses data <= t.

    Warmup: bar 0 carries no previous close, so its TR and +/-DM are
    excluded from the Wilder seed; the first valid DI is at 0-based index
    ``n`` and ADX (the Wilder average of DX) first appears at ``2n-1``
    (index 27 for n=14) -- documented, not shortened, to avoid
    partial-window bias."""
    tr = _true_range(high, low, close).copy()
    if len(tr):
        tr.iloc[0] = np.nan  # no previous close -> not a complete TR
    plus_dm, minus_dm = _directional_movement(high, low)
    atr_w = _wilder_mean(tr, n)
    plus_di = 100.0 * _wilder_mean(plus_dm, n) / atr_w.replace(0.0, np.nan)
    minus_di = 100.0 * _wilder_mean(minus_dm, n) / atr_w.replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx_v = _wilder_mean(dx, n)
    return pd.DataFrame({"adx": adx_v, "plus_di": plus_di,
                         "minus_di": minus_di}, index=close.index)


def bollinger(close: pd.Series, n: int = 20, n_std: float = 2.0,
              ddof: int = 1) -> pd.DataFrame:
    """Bollinger Bands. Bar t uses closes [t-n+1, t].

    ``ddof`` selects the dispersion convention: 1 (default, SAMPLE std,
    current behaviour) or 0 (POPULATION std, the TA-Lib / most-platform
    convention; bands are ~2.6% narrower at n=20 and %B differs by up to
    0.022). It is a documented choice, not an accident, and is recorded in
    the feature registry so a consumer comparing against another tool knows
    which convention produced the number.
    %B = (close - lower) / (upper - lower); NaN when band width is 0.
    """
    if ddof not in (0, 1):
        raise ValueError("ddof must be 0 (population) or 1 (sample)")
    mid = sma(close, n)
    std = close.rolling(window=n, min_periods=n).std(ddof=ddof)
    upper = mid + n_std * std
    lower = mid - n_std * std
    width = (upper - lower).replace(0.0, np.nan)
    return pd.DataFrame({
        "bb_mid": mid,
        "bb_upper": upper,
        "bb_lower": lower,
        "bb_pctb": (close - lower) / width,
        "bb_bandwidth": (upper - lower) / mid.replace(0.0, np.nan),
    }, index=close.index)


def keltner(high: pd.Series, low: pd.Series, close: pd.Series,
            ema_n: int = 20, atr_n: int = 10,
            mult: float = 2.0) -> pd.DataFrame:
    """Keltner Channels: EMA(n) +/- mult * ATR(atr_n). Trailing only."""
    mid = ema(close, ema_n)
    a = atr(high, low, close, atr_n)
    return pd.DataFrame({
        "kc_mid": mid,
        "kc_upper": mid + mult * a,
        "kc_lower": mid - mult * a,
    }, index=close.index)


def ichimoku(high: pd.Series, low: pd.Series, close: pd.Series,
             tenkan: int = 9, kijun: int = 26,
             senkou_b: int = 52) -> pd.DataFrame:
    """Ichimoku components. All midpoint calculations are trailing.

    LEAKAGE NOTE: Senkou Span A/B are shifted +26 bars as a *display*
    convention. The value plotted at bar ``t`` was fully determined by bars
    ``<= t-26`` — it was already known at ``t-26``. No future data is used.
    Chikou (lagging span) is returned UNSHIFTED (= close[t]); applying the
    textbook -26 display shift would reference close[t+26] (future data) and
    is left to the caller with this warning.
    """
    tenkan_sen = (high.rolling(tenkan, min_periods=tenkan).max()
                  + low.rolling(tenkan, min_periods=tenkan).min()) / 2.0
    kijun_sen = (high.rolling(kijun, min_periods=kijun).max()
                 + low.rolling(kijun, min_periods=kijun).min()) / 2.0
    senkou_a = ((tenkan_sen + kijun_sen) / 2.0).shift(26)
    senkou_b_raw = (high.rolling(senkou_b, min_periods=senkou_b).max()
                    + low.rolling(senkou_b, min_periods=senkou_b).min()) / 2.0
    senkou_b_s = senkou_b_raw.shift(26)
    return pd.DataFrame({
        "ichimoku_tenkan": tenkan_sen,
        "ichimoku_kijun": kijun_sen,
        "ichimoku_senkou_a": senkou_a,   # value at t known since t-26
        "ichimoku_senkou_b": senkou_b_s,  # value at t known since t-26
        "ichimoku_chikou": close.copy(),  # UNSHIFTED — see leakage note
    }, index=close.index)


def vwap(df: pd.DataFrame) -> pd.Series:
    """Volume-Weighted Average Price.

    * Intraday input (multiple bars per calendar day): VWAP resets at each
      session start — the standard definition.
    * Daily input (one bar per day): there is no intraday session to reset
      on, so this returns an ANCHORED cumulative VWAP from the first bar.
      This fallback is documented in the series name/attrs and must not be
      presented as a true session VWAP.

    Typical price = (H+L+C)/3, computed bar-by-bar (no future data).
    """
    df = _check_ohlcv(df)
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].astype(float)
    n_per_day = df.groupby(df.index.date).size()
    intraday = bool((n_per_day > 1).any())
    if intraday:
        day = df.index.date
        cum_pv = (typical * vol).groupby(day).cumsum()
        cum_v = vol.groupby(day).cumsum()
    else:
        cum_pv = (typical * vol).cumsum()
        cum_v = vol.cumsum()
    out = cum_pv / cum_v.replace(0.0, np.nan)
    out.attrs["vwap_mode"] = "session" if intraday else "anchored_daily_fallback"
    return out


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume. Cumulative; bar t uses sign(close[t]-close[t-1])
    * volume[t] — no future data."""
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume.astype(float)).cumsum()


def realized_volatility(close: pd.Series, n: int = 20,
                        annualize: float = 252.0) -> pd.Series:
    """Annualized realized volatility of log returns over trailing n bars."""
    logret = np.log(close / close.shift(1))
    return logret.rolling(window=n, min_periods=n).std(ddof=1) * np.sqrt(annualize)
