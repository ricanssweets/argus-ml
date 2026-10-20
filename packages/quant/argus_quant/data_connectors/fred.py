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

DOCUMENTED LIMITATION (vintage / revision bias): the endpoint serves the
CURRENTLY published vintage of each series. Point-in-time filtering here is
by OBSERVATION date, not by release date, so an ``as_of`` in the past sees
figures that were subsequently revised (CPI, UNRATE and the aggregates are
revised). No ALFRED vintage is used. ``macro_features`` echoes this in
``obs_dates``/``cache_status`` but cannot correct it; callers comparing
historic ``as_of`` values to live trading must treat them as revised.
Cache: a fresh CSV is served as-is; on a network/schema failure an expired
but valid cache is served with ``attrs["cache"]["stale"] = True``; payloads
are validated before being cached, atomically.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import time

import pandas as pd
import requests

log = logging.getLogger("argus_quant.fred")

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

# Cache location: configurable, never a bare developer home path. The
# default keeps the documented deployment layout (shared with scripts/);
# ARGUS_QUANT_CACHE_DIR / ARGUS_QUANT_FRED_CACHE relocate it.
_BASE_CACHE = os.path.expanduser(
    os.environ.get("ARGUS_QUANT_CACHE_DIR") or "~/workspace/argus/data")
DEFAULT_CACHE_DIR = os.path.expanduser(
    os.environ.get("ARGUS_QUANT_FRED_CACHE")
    or os.path.join(_BASE_CACHE, "fred"))
_TTL = 1 * 86400  # refresh daily; series are revised, cache keeps us polite
_POLITE_SLEEP = 0.25
_RETRIES = 2            # attempts (not counting the polite sleep)
_RETRY_BACKOFF = 0.5    # seconds, multiplied by the attempt number
_EXPECTED_COLUMNS = 2   # FRED graph CSV: date,value


class FredSchemaError(ValueError):
    """FRED returned something that is not the documented 2-column CSV."""


def _parse_csv(raw: bytes, source: str) -> pd.DataFrame:
    """Parse + validate a FRED graph CSV. Raises :class:`FredSchemaError`.

    The schema is checked BEFORE anything is cached: a 200 HTML error page
    (one column) or a changed column set used to be written to disk first
    and then blow up on the positional ``df.columns = [...]`` rename, so
    the bad payload stayed cached for a full TTL.
    """
    try:
        df = pd.read_csv(io.BytesIO(raw))
    except (ValueError, pd.errors.ParserError) as exc:
        raise FredSchemaError(
            f"{source}: not a parseable CSV ({type(exc).__name__}: {exc}); "
            f"first bytes={raw[:60]!r}") from exc
    if df.shape[1] != _EXPECTED_COLUMNS:
        raise FredSchemaError(
            f"{source}: expected {_EXPECTED_COLUMNS} columns (date,value), "
            f"got {df.shape[1]} ({list(df.columns)[:4]}); "
            f"first bytes={raw[:60]!r}")
    df = df.copy()
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["date"])
    if df.empty:
        raise FredSchemaError(f"{source}: no parseable date rows")
    df = df.dropna(subset=["value"]).set_index("date").sort_index()
    return df[["value"]]


def _write_atomic(path: str, raw: bytes) -> None:
    """Write the cache atomically (tmp + os.replace)."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".fred-", suffix=".csv.part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _fetch(series_id: str) -> bytes:
    """GET the CSV with a small retry/backoff on transient failures."""
    last = None
    for attempt in range(1, _RETRIES + 1):
        time.sleep(_POLITE_SLEEP)
        try:
            resp = requests.get(_URL.format(sid=series_id), timeout=30)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised
            last = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            transient = status is None or status == 429 or status >= 500
            if attempt < _RETRIES and transient:
                time.sleep(_RETRY_BACKOFF * attempt)
                continue
            raise
    raise last  # pragma: no cover - loop either returns or raises


def get_series(series_id: str, cache_dir: str = DEFAULT_CACHE_DIR,
               allow_stale: bool = True) -> pd.DataFrame:
    """Tidy series: DatetimeIndex 'date', single float column 'value'.

    Rows with FRED's "." missing marker are dropped. Raises KeyError for
    unknown series ids, :class:`FredSchemaError` for a payload that is not
    the documented 2-column CSV, and the underlying request error when the
    network fails AND no cache is available.

    * A fresh cache (< TTL) is served as-is.
    * On a network/schema failure an EXPIRED but valid cache is served with
      ``df.attrs["cache"]["stale"] = True`` (+ its age) instead of failing
      the whole macro read -- controlled by ``allow_stale``.
    * The payload is parsed and validated BEFORE being written, atomically,
      so a non-CSV 200 response can never poison the cache.
    ``df.attrs["cache"]`` always reports {"source", "stale", "age_seconds"}.
    """
    if series_id not in SERIES:
        raise KeyError(f"unknown FRED series {series_id!r}; see fred.SERIES")
    path = os.path.join(cache_dir, f"{series_id}.csv")
    cached, age = None, None
    if os.path.exists(path):
        try:
            age = time.time() - os.path.getmtime(path)
            with open(path, "rb") as fh:
                cached = _parse_csv(fh.read(), f"cache {path}")
        except (OSError, FredSchemaError) as exc:
            log.warning("FRED cache %s unusable (%s) - refetching",
                        path, exc)
            cached = None
            try:
                os.remove(path)
            except OSError:
                pass
    if cached is not None and age is not None and age < _TTL:
        cached.attrs["cache"] = {"source": "cache", "stale": False,
                                 "age_seconds": round(age, 1)}
        return cached
    try:
        raw = _fetch(series_id)
        df = _parse_csv(raw, f"network {series_id}")
    except Exception as exc:  # noqa: BLE001 - stale fallback decision point
        if cached is not None and allow_stale:
            log.warning("FRED fetch failed for %s (%s: %s) - serving STALE "
                        "cache %s (age %.0fs)", series_id,
                        type(exc).__name__, exc, path, age or 0.0)
            cached.attrs["cache"] = {"source": "stale_cache", "stale": True,
                                     "age_seconds": round(age or 0.0, 1)}
            return cached
        raise
    _write_atomic(path, raw)
    df.attrs["cache"] = {"source": "network", "stale": False,
                         "age_seconds": 0.0}
    return df


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
    cache_status = {sid: dict(df.attrs.get("cache") or {})
                    for sid, df in data.items()}

    def val(sid):
        v = _last(data[sid], as_of)
        if v is not None:
            obs[sid] = data[sid].loc[data[sid].index <= as_of].index[-1].date().isoformat()
        return v

    feats: dict = {"as_of": str(as_of.date()), "obs_dates": obs,
                   "cache_status": cache_status}

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
