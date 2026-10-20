"""Options connector via yfinance (free, delayed ~15 min, no key).

``get_chain(ticker)`` downloads the nearest few expiries' calls/puts and
caches them under ~/workspace/argus/data/options/. On ANY failure
(network, rate limit, yfinance uninstallable) it returns None -- the
caller (Engine F) then reports data_status MISSING. The chain is NEVER
presented as real-time: Engine F marks results LIKELY with the note
"delayed chain, not real-time".

``chain_features(chain, price_df)`` turns a cached chain + price history
into the feature dict Engine F consumes:

  atm_iv            IV of the nearest-expiry ATM straddle (call/put avg)
  hv20 / hv60       annualized historical vol of log returns, 20/60d
  iv_rank           (iv - min_1y)/(max_1y - min_1y) of daily ATM IV history
  iv_percentile     percentile rank of current ATM IV vs trailing 252d
  put_call_oi       sum(put OI)/sum(call OI) over included expiries
  put_call_vol      sum(put volume)/sum(call volume) over included expiries
  expected_move_pct (ATM call mid + ATM put mid)/price, nearest expiry
  skew_proxy        IV(put, K~0.90*S) - IV(call, K~1.10*S), nearest expiry
                    (moneyness proxy; yfinance chains carry no delta)
  unusual           [{expiry, kind, strike, volume, openInterest}] where
                    volume > 3 * openInterest

IV history accumulates in data/options/iv_history/{ticker}.csv (one row
per fetch date); iv_rank/iv_percentile are None until >= 20 history
points exist -- never faked from a single observation. Both are computed
POINT-IN-TIME: only IV rows with ``date <= chain_date`` are used, and the
HV windows only see price bars ``<= chain_date``.

Lookbacks: HV windows 20/60 trading days; IV history trailing 252
sessions; unusual-activity is a same-snapshot cross-section (no lookback).
"""

from __future__ import annotations

import json
import os
import time

import numpy as np
import pandas as pd

# Cache location: configurable (ARGUS_QUANT_CACHE_DIR / ARGUS_QUANT_OPTIONS_CACHE)
# with the documented deployment default.
_BASE_CACHE = os.path.expanduser(
    os.environ.get("ARGUS_QUANT_CACHE_DIR") or "~/workspace/argus/data")
DEFAULT_CACHE_DIR = os.path.expanduser(
    os.environ.get("ARGUS_QUANT_OPTIONS_CACHE")
    or os.path.join(_BASE_CACHE, "options"))
_POLITE_SLEEP = 0.5
_MAX_EXPIRIES = 6  # nearest expiries are where positioning signal lives


def _jsonable(obj):
    """Recursively convert DataFrame records to strict-JSON values."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj


def _read_cache(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        # corrupt/partial cache (e.g. interrupted write): drop it, refetch
        try:
            os.remove(path)
        except OSError:
            pass
        return None


def get_chain(ticker: str, cache_dir: str = DEFAULT_CACHE_DIR,
              chain_date: str | None = None):
    """Download nearest expiries' chains; None on any failure.

    ``chain_date`` lets callers pin the snapshot date (tests, or an
    as_of < today); otherwise today (UTC) is used and the result is
    cached for the day.
    """
    ticker = ticker.upper()
    day = chain_date or pd.Timestamp.now(tz="UTC").date().isoformat()
    path = os.path.join(cache_dir, f"{ticker}_{day}.json")
    if os.path.exists(path):
        cached = _read_cache(path)
        if cached is not None:
            return cached
    try:
        import yfinance as yf
    except ImportError:
        return None
    try:
        t = yf.Ticker(ticker)
        time.sleep(_POLITE_SLEEP)
        exps = list(t.options)[:_MAX_EXPIRIES]
        if not exps:
            return None
        chains = {}
        for exp in exps:
            time.sleep(_POLITE_SLEEP)
            ch = t.option_chain(exp)
            chains[exp] = {
                "calls": _jsonable(ch.calls.to_dict(orient="records")),
                "puts": _jsonable(ch.puts.to_dict(orient="records")),
            }
        time.sleep(_POLITE_SLEEP)
        hist = t.history(period="5d", auto_adjust=False)
        price = float(hist["Close"].iloc[-1]) if not hist.empty else None
        out = {"ticker": ticker, "chain_date": day,
               "underlying_price": price, "expirations": chains}
        os.makedirs(cache_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(out, fh)
        os.replace(tmp, path)  # atomic: never leaves a partial cache file
        return out
    except Exception:
        return None  # rate limit / network / parse failure -> MISSING path


def _df(records) -> pd.DataFrame:
    df = pd.DataFrame(records)
    for c in ("strike", "lastPrice", "bid", "ask", "volume", "openInterest",
              "impliedVolatility"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _mid(df: pd.DataFrame) -> pd.Series:
    mid = (df["bid"] + df["ask"]) / 2.0
    return mid.where(mid > 0, df["lastPrice"])


def _record_iv_history(ticker: str, chain_date: str, atm_iv: float,
                       cache_dir: str = DEFAULT_CACHE_DIR) -> pd.DataFrame:
    """Append/refresh the ATM IV for ``chain_date``; return the history.

    The ``date`` column is normalised to ``YYYY-MM-DD`` strings on every
    read and write, so the mixed str/Timestamp encodings produced by
    pandas' CSV round-trip (``"2024-01-02 00:00:00"`` vs ``"2024-01-03"``)
    can never turn the column into a non-datetime dtype and crash the
    reader. Rows are unique by date, sorted and capped at 252. Any
    unreadable/unwritable cache degrades to an empty history instead of
    raising -- the caller then reports iv_rank/percentile as None (MISSING),
    never a fabricated number.
    """
    empty = pd.DataFrame(columns=["date", "atm_iv"])
    d = os.path.join(cache_dir, "iv_history")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return empty
    path = os.path.join(d, f"{ticker}.csv")
    day = pd.Timestamp(chain_date).strftime("%Y-%m-%d")

    hist = empty
    if os.path.exists(path):
        try:
            raw = pd.read_csv(path)
        except (OSError, ValueError, TypeError):
            raw = empty
        if "date" in raw.columns and "atm_iv" in raw.columns:
            hist = raw
    hist = hist.copy()
    hist["date"] = hist["date"].astype(str).str.slice(0, 10)
    hist["atm_iv"] = pd.to_numeric(hist["atm_iv"], errors="coerce")
    hist = hist[hist["date"].str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)]
    hist = hist.dropna(subset=["atm_iv"])
    hist = hist[hist["date"] != day]           # refresh, never duplicate, today
    hist = pd.concat([hist, pd.DataFrame([{"date": day,
                                           "atm_iv": float(atm_iv)}])],
                     ignore_index=True)
    hist = (hist.sort_values("date").drop_duplicates(subset=["date"], keep="last")
            .tail(252).reset_index(drop=True))
    # Atomic write (tmp + os.replace): an interrupted write used to leave a
    # truncated CSV that the next read could reject outright.
    try:
        tmp = path + ".tmp"
        hist.to_csv(tmp, index=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except (OSError, UnboundLocalError):
            pass
    return hist[["date", "atm_iv"]]


def chain_features(chain: dict, price_df: pd.DataFrame,
                   cache_dir: str = DEFAULT_CACHE_DIR) -> dict | None:
    """Feature dict for Engine F from a cached chain + OHLCV price history.

    POINT-IN-TIME CONTRACT: the price frame is truncated to bars
    ``<= chain_date`` before any return/volatility is computed, and the
    IV history used for iv_rank/iv_percentile is filtered to
    ``date <= chain_date``. The underlying price used for the moneyness
    calculations is the last close at or before ``chain_date`` (falling
    back to the snapshot's ``underlying_price`` only when no such close is
    supplied). Appending future bars to ``price_df`` therefore cannot
    change the feature vector.
    """
    if not chain or not chain.get("expirations"):
        return None
    chain_date = pd.Timestamp(chain["chain_date"])
    px = price_df
    if isinstance(px, pd.DataFrame) and isinstance(px.index, pd.DatetimeIndex):
        px = px.loc[px.index <= chain_date]
    closes = None
    if isinstance(px, pd.DataFrame) and "close" in px.columns:
        closes = px["close"].dropna()
    pit_price = float(closes.iloc[-1]) if closes is not None and len(closes) else None
    price = pit_price if pit_price else chain.get("underlying_price")
    if not price or price <= 0:
        return None

    exps = sorted(chain["expirations"])
    first = exps[0]
    calls, puts = _df(chain["expirations"][first]["calls"]), \
        _df(chain["expirations"][first]["puts"])
    if calls.empty or puts.empty:
        return None

    # --- ATM straddle on nearest expiry ----------------------------------
    for dframe in (calls, puts):
        dframe["dist"] = (dframe["strike"] - price).abs()
    c_atm = calls.loc[calls["dist"].idxmin()]
    p_atm = puts.loc[puts["dist"].idxmin()]
    atm_iv = float(np.nanmean([c_atm["impliedVolatility"],
                               p_atm["impliedVolatility"]]))
    exp_move = float((_mid(calls).loc[c_atm.name] +
                      _mid(puts).loc[p_atm.name]) / price)
    dte = max((pd.Timestamp(first) - chain_date).days, 1)

    # --- historical vol (truncated to chain_date) --------------------------
    if closes is not None and len(closes):
        rets = np.log(closes / closes.shift(1)).dropna()
    else:
        rets = pd.Series(dtype=float)
    hv20 = float(rets.tail(20).std() * np.sqrt(252)) if len(rets) >= 20 else None
    hv60 = float(rets.tail(60).std() * np.sqrt(252)) if len(rets) >= 60 else None

    # --- IV rank / percentile vs trailing history (<= chain_date) -----------
    iv_rank = iv_pct = None
    if np.isfinite(atm_iv):
        try:
            hist = _record_iv_history(chain["ticker"], chain["chain_date"],
                                      atm_iv, cache_dir)
        except (OSError, ValueError, TypeError, AttributeError):
            hist = pd.DataFrame(columns=["date", "atm_iv"])
        if not hist.empty:
            cutoff = chain_date.strftime("%Y-%m-%d")
            hist = hist[hist["date"].astype(str).str.slice(0, 10) <= cutoff]
        ivs = pd.to_numeric(hist["atm_iv"], errors="coerce").dropna()
        if len(ivs) >= 20:
            lo, hi = float(ivs.min()), float(ivs.max())
            iv_rank = (atm_iv - lo) / (hi - lo) if hi > lo else 0.5
            iv_pct = float((ivs <= atm_iv).mean())

    # --- put/call ratios + unusual activity over all included expiries -----
    pc_oi = pc_vol = None
    oi_c = oi_p = vol_c = vol_p = 0.0
    unusual = []
    for exp in exps:
        for kind, key in (("call", "calls"), ("put", "puts")):
            df = _df(chain["expirations"][exp][key])
            if df.empty:
                continue
            oi = float(df["openInterest"].fillna(0).sum())
            vo = float(df["volume"].fillna(0).sum())
            if kind == "call":
                oi_c += oi; vol_c += vo
            else:
                oi_p += oi; vol_p += vo
            hot = df[(df["volume"].fillna(0) > 3 * df["openInterest"].fillna(0)) &
                      (df["openInterest"].fillna(0) > 0)]
            for _, r in hot.iterrows():
                unusual.append({"expiry": exp, "kind": kind,
                                "strike": float(r["strike"]),
                                "volume": float(r["volume"]),
                                "openInterest": float(r["openInterest"])})
    if oi_c > 0:
        pc_oi = oi_p / oi_c
    if vol_c > 0:
        pc_vol = vol_p / vol_c
    n_call_un = sum(1 for u in unusual if u["kind"] == "call")
    n_put_un = sum(1 for u in unusual if u["kind"] == "put")

    # --- skew proxy: 25-delta approx via moneyness ---------------------------
    skew = None
    pk = puts.iloc[(puts["strike"] - 0.90 * price).abs().argsort()[:1]]
    ck = calls.iloc[(calls["strike"] - 1.10 * price).abs().argsort()[:1]]
    if not pk.empty and not ck.empty:
        piv, civ = pk["impliedVolatility"].iloc[0], ck["impliedVolatility"].iloc[0]
        if np.isfinite(piv) and np.isfinite(civ):
            skew = float(piv - civ)

    return {
        "chain_date": chain["chain_date"],
        "underlying_price": round(price, 2),
        "atm_iv": round(atm_iv, 4) if np.isfinite(atm_iv) else None,
        "hv20": round(hv20, 4) if hv20 else None,
        "hv60": round(hv60, 4) if hv60 else None,
        "iv_rank": round(iv_rank, 3) if iv_rank is not None else None,
        "iv_percentile": round(iv_pct, 3) if iv_pct is not None else None,
        "put_call_oi": round(pc_oi, 3) if pc_oi else None,
        "put_call_vol": round(pc_vol, 3) if pc_vol else None,
        "expected_move_pct": round(exp_move * 100, 2) if np.isfinite(exp_move) else None,
        "dte_days": dte,
        "skew_proxy": round(skew, 4) if skew is not None else None,
        "unusual_count": len(unusual),
        "unusual_net_call": (n_call_un - n_put_un) if unusual else 0,
        "unusual": unusual[:25],
        "expiries_used": len(exps),
    }
