"""FRED connector (Federal Reserve Bank of St. Louis, free, no key).

Endpoint: https://fred.stlouisfed.org/graph/fredgraph.csv?id=SERIES
CSVs are cached under ~/workspace/argus/data/fred/. Values are as
published; FRED marks missing observations with "." which we drop.

Series used by the macro engine (D):

  FEDFUNDS      Effective federal funds rate, % (monthly)
  DGS10         10-year Treasury constant maturity, % (daily)
  DGS2          2-year Treasury constant maturity, % (daily)
  CPIAUCSL      CPI for All Urban Consumers, index (monthly)
  UNRATE        Unemployment rate, % (monthly)
  BAMLH0A0HYM2  ICE BofA US High Yield option-adjusted spread, % (daily)
  DTWEXBGS      Nominal broad USD index, Jan-2006=100 (daily)
  DCOILWTICO    WTI crude oil spot price, $/bbl (daily)

``macro_features(as_of)`` returns the seven features Engine D consumes.
Every feature uses observations <= as_of only. Lookbacks are documented
per feature in the function docstring.
"""

from __future__ import annotations

import os
import time

import pandas as pd
import requests

SERIES = {
    "FEDFUNDS": "Effective federal funds rate, % (monthly)",
    "DGS10": "10Y Treasury constant maturity, % (daily)",
    "DGS2": "2Y Treasury constant maturity, % (daily)",
    "CPIAUCSL": "CPI All Urban Consumers, index (monthly)",
    "UNRATE": "Unemployment rate, % (monthly)",
    "BAMLH0A0HYM2": "ICE BofA US High Yield OAS, % (daily)",
    "DTWEXBGS": "Nominal broad USD index, Jan-2006=100 (daily)",
    "DCOILWTICO": "WTI crude spot, $/bbl (daily)",
}

_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
DEFAULT_CACHE_DIR = os.path.expanduser("~/workspace/argus/data/fred")
_TTL = 1 * 86400  # refresh daily; series are revised, cache keeps us polite
_POLITE_SLEEP = 0.25


def get_series(series_id: str, cache_dir: str = DEFAULT_CACHE_DIR) -> pd.DataFrame:
    """Tidy series: DatetimeIndex 'date', single float column 'value'.

    Rows with FRED's "." missing marker are dropped. Raises KeyError for
    unknown series ids and requests.HTTPError on network failure.
    """
    if series_id not in SERIES:
        raise KeyError(f"unknown FRED series {series_id!r}; see fred.SERIES")
    path = os.path.join(cache_dir, f"{series_id}.csv")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < _TTL:
        df = pd.read_csv(path)
    else:
        time.sleep(_POLITE_SLEEP)
        resp = requests.get(_URL.format(sid=series_id), timeout=30)
        resp.raise_for_status()
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(resp.content)
        df = pd.read_csv(path)
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"]).set_index("date").sort_index()
    return df[["value"]]


def _last(s: pd.DataFrame, as_of: pd.Timestamp):
    s = s.loc[s.index <= as_of]
    return float(s["value"].iloc[-1]) if not s.empty else None


def macro_features(as_of, cache_dir: str = DEFAULT_CACHE_DIR) -> dict:
    """Seven macro features at ``as_of`` (all observations <= as_of).

    Lookbacks:
      real_10y            DGS10(t) - CPI yoy(t); CPI yoy needs 13 monthly obs
      curve_10y_2y        DGS10(t) - DGS2(t); point-in-time
      inflation_surprise  CPI yoy(t) - mean(CPI yoy over trailing 12m); 24m CPI
      unrate_momentum     UNRATE(t) - UNRATE(t-3m); 4 monthly obs
      hy_spread_z         z-score of HY OAS vs trailing 252 daily obs
      dollar_trend        63-trading-day return of DTWEXBGS
      oil_trend           63-trading-day return of DCOILWTICO

    Missing inputs yield None for that feature (never imputed). The dict
    also carries ``as_of`` and per-series observation dates for the audit
    trail.
    """
    as_of = pd.Timestamp(as_of)
    data = {sid: get_series(sid, cache_dir) for sid in SERIES}
    obs = {}

    def val(sid):
        v = _last(data[sid], as_of)
        if v is not None:
            obs[sid] = data[sid].loc[data[sid].index <= as_of].index[-1].date().isoformat()
        return v

    feats: dict = {"as_of": str(as_of.date()), "obs_dates": obs}

    # --- real 10y yield -------------------------------------------------
    dgs10, cpi = val("DGS10"), None
    cpi_s = data["CPIAUCSL"].loc[data["CPIAUCSL"].index <= as_of]["value"]
    if len(cpi_s) >= 13:
        cpi_yoy = cpi_s.iloc[-1] / cpi_s.iloc[-13] - 1.0
        feats["cpi_yoy"] = round(float(cpi_yoy) * 100, 3)
        if dgs10 is not None:
            feats["real_10y"] = round(dgs10 - feats["cpi_yoy"], 3)
        # inflation surprise vs trailing 12m mean of yoy (needs 24m of CPI)
        if len(cpi_s) >= 25:
            yoy = cpi_s / cpi_s.shift(12) - 1.0
            yoy = yoy.dropna().tail(13)  # current + 12 trailing
            if len(yoy) == 13:
                feats["inflation_surprise"] = round(
                    float((yoy.iloc[-1] - yoy.iloc[:-1].mean()) * 100), 3)

    # --- curve -----------------------------------------------------------
    dgs2 = val("DGS2")
    if dgs10 is not None and dgs2 is not None:
        feats["curve_10y_2y"] = round(dgs10 - dgs2, 3)

    # --- unemployment momentum -------------------------------------------
    u = data["UNRATE"].loc[data["UNRATE"].index <= as_of]["value"]
    if len(u) >= 4:
        feats["unrate_momentum"] = round(float(u.iloc[-1] - u.iloc[-4]), 3)
        feats["unrate"] = round(float(u.iloc[-1]), 2)

    # --- HY spread z-score ------------------------------------------------
    hy = data["BAMLH0A0HYM2"].loc[data["BAMLH0A0HYM2"].index <= as_of]["value"]
    if len(hy) >= 252:
        w = hy.tail(252)
        std = float(w.std())
        feats["hy_spread"] = round(float(w.iloc[-1]), 3)
        feats["hy_spread_z"] = round(float((w.iloc[-1] - w.mean()) / std), 3) if std > 0 else 0.0

    # --- dollar / oil 63d trends ------------------------------------------
    for sid, key in (("DTWEXBGS", "dollar_trend"), ("DCOILWTICO", "oil_trend")):
        s = data[sid].loc[data[sid].index <= as_of]["value"]
        if len(s) >= 64:
            feats[key] = round(float(s.iloc[-1] / s.iloc[-64] - 1.0), 4)

    return feats
