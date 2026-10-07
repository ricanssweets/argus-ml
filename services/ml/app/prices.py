"""Real daily OHLCV fetching for the ML service (Yahoo Finance, no key).

Shared by routers that need real market prices. Disk cache under
~/workspace/argus/data/raw/<TICKER>.csv (shared with scripts/).

Raises PriceFetchError on failure — callers translate it into an HTTP
error envelope. Prices are NEVER synthesized here.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from pathlib import Path

import pandas as pd

log = logging.getLogger("argus.ml.prices")

DATA_DIR = Path.home() / "workspace" / "argus" / "data" / "raw"
DATA_DIR.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}


class PriceFetchError(Exception):
    pass


def _cache_path(ticker: str) -> Path:
    return DATA_DIR / (ticker.replace("^", "").upper() + ".csv")


def fetch_yahoo_daily(ticker: str, period1: int | None = None,
                      period2: int | None = None,
                      use_cache: bool = True) -> pd.DataFrame:
    """Daily OHLCV for ``ticker`` (use "^VIX" for the VIX index).

    Cache-first: a cached CSV is returned as-is. Pass use_cache=False
    to force a refresh. Raises PriceFetchError (never empty/synthetic).
    """
    t = ticker.upper()
    cp = _cache_path(t)
    if use_cache and cp.exists():
        df = pd.read_csv(cp, index_col=0, parse_dates=True)
        if not df.empty and "close" in df.columns:
            return df
    if period2 is None:
        period2 = int(time.time())
    if period1 is None:
        period1 = period2 - 6 * 365 * 24 * 3600  # ~6y default
    url_ticker = t.replace("^", "%5E")
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{url_ticker}"
           f"?period1={period1}&period2={period2}&interval=1d"
           f"&events=div%2Csplit")
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.load(r)
        res = payload["chart"]["result"][0]
    except Exception as exc:
        raise PriceFetchError(f"Yahoo fetch failed for {t}: "
                              f"{type(exc).__name__}") from exc
    ts = pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert(
        "America/New_York").tz_localize(None)
    q = res["indicators"]["quote"][0]
    adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose")
    df = pd.DataFrame({
        "open": q["open"], "high": q["high"], "low": q["low"],
        "close": q["close"], "volume": q["volume"],
        "adj_close": adj if adj else q["close"],
    }, index=ts)
    df = df.dropna(subset=["close"])
    df.index.name = "time"
    if df.empty:
        raise PriceFetchError(f"Yahoo returned no bars for {t}")
    df.to_csv(cp)
    log.info("fetched %s: %d bars (%s -> %s)", t, len(df),
             df.index[0].date(), df.index[-1].date())
    return df


def fetch_many(tickers: list[str], use_cache: bool = True
               ) -> dict[str, pd.DataFrame]:
    """Fetch several tickers; returns {TICKER: df} for successes only."""
    out = {}
    for t in tickers:
        out[t.upper()] = fetch_yahoo_daily(t, use_cache=use_cache)
        time.sleep(0.5)  # be polite to Yahoo
    return out
