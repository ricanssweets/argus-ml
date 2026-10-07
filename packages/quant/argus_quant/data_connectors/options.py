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
points exist -- never faked from a single observation.

Lookbacks: HV windows 20/60 trading days; IV history trailing 252
sessions; unusual-activity is a same-snapshot cross-section (no lookback).
"""

from __future__ import annotations

import json
import os
import time

import numpy as np
import pandas as pd

DEFAULT_CACHE_DIR = os.path.expanduser("~/workspace/argus/data/options")
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
    day = chain_date or pd.Timestamp.utcnow().date().isoformat()
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
    """Append today's ATM IV; return the trailing history (<= 252 rows)."""
    d = os.path.join(cache_dir, "iv_history")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{ticker}.csv")
    hist = pd.read_csv(path, parse_dates=["date"]) if os.path.exists(path) else \
        pd.DataFrame(columns=["date", "atm_iv"])
    if hist.empty or str(hist["date"].max().date()) != chain_date:
        hist = pd.concat([hist, pd.DataFrame(
            [{"date": chain_date, "atm_iv": atm_iv}])], ignore_index=True)
        hist.to_csv(path, index=False)
    return hist.tail(252)


def chain_features(chain: dict, price_df: pd.DataFrame,
                   cache_dir: str = DEFAULT_CACHE_DIR) -> dict | None:
    """Feature dict for Engine F from a cached chain + OHLCV price history."""
    if not chain or not chain.get("expirations"):
        return None
    price = chain.get("underlying_price")
    if price is None:
        closes = price_df["close"] if "close" in price_df.columns else None
        price = float(closes.iloc[-1]) if closes is not None and len(closes) else None
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
    dte = max((pd.Timestamp(first) - pd.Timestamp(chain["chain_date"])).days, 1)

    # --- historical vol ----------------------------------------------------
    rets = np.log(price_df["close"] / price_df["close"].shift(1)).dropna()
    hv20 = float(rets.tail(20).std() * np.sqrt(252)) if len(rets) >= 20 else None
    hv60 = float(rets.tail(60).std() * np.sqrt(252)) if len(rets) >= 60 else None

    # --- IV rank / percentile vs trailing history ---------------------------
    iv_rank = iv_pct = None
    if np.isfinite(atm_iv):
        hist = _record_iv_history(chain["ticker"], chain["chain_date"], atm_iv,
                                  cache_dir)
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
