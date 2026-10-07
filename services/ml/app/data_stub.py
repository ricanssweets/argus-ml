"""In-memory STUB data layer — DEMO ONLY, clearly labeled.

No live database is connected in this build (Phase 0). Every payload this
module produces carries data_status="UNCONFIRMED" and must be surfaced to
callers with demo/synthetic labeling. Nothing here is real market data and
nothing here may be presented as a real prediction.

Deterministic: all synthetic series are seeded so responses are stable
across restarts (good for tests, bad for anyone mistaking this for a feed).
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np

STUB_LABEL = "DEMO/STUB — synthetic data, not real market data"
DATA_STATUS = "UNCONFIRMED"

HORIZONS = [1, 3, 5, 10, 20, 63, 126, 252]

_ASSETS: Dict[str, Dict[str, Any]] = {
    "SPY": {
        "ticker": "SPY",
        "name": "SPDR S&P 500 ETF Trust",
        "asset_type": "ETF",
        "exchange": "NYSE Arca",
        "currency": "USD",
        "sector": "Broad Market",
        "industry": "Index ETF",
        "is_active": True,
        "price": 645.20,
        "prev_close": 641.85,
        "market_cap": 590_000_000_000,
        "expense_ratio": 0.000945,
        "issuer": "State Street Global Advisors",
    },
    "NVDA": {
        "ticker": "NVDA",
        "name": "NVIDIA Corporation",
        "asset_type": "STOCK",
        "exchange": "NASDAQ",
        "currency": "USD",
        "sector": "Technology",
        "industry": "Semiconductors",
        "is_active": True,
        "price": 178.44,
        "prev_close": 175.90,
        "market_cap": 4_350_000_000_000,
        "pe": 52.3,
        "eps": 3.41,
    },
    "AAPL": {
        "ticker": "AAPL",
        "name": "Apple Inc.",
        "asset_type": "STOCK",
        "exchange": "NASDAQ",
        "currency": "USD",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "is_active": True,
        "price": 262.10,
        "prev_close": 260.55,
        "market_cap": 3_890_000_000_000,
        "pe": 38.7,
        "eps": 6.77,
    },
    "QQQ": {
        "ticker": "QQQ",
        "name": "Invesco QQQ Trust",
        "asset_type": "ETF",
        "exchange": "NASDAQ",
        "currency": "USD",
        "sector": "Broad Market",
        "industry": "Index ETF",
        "is_active": True,
        "price": 582.33,
        "prev_close": 579.10,
        "market_cap": 340_000_000_000,
        "expense_ratio": 0.0020,
        "issuer": "Invesco",
    },
}

_ETF_HOLDINGS: Dict[str, List[Dict[str, Any]]] = {
    "SPY": [
        {"ticker": "NVDA", "name": "NVIDIA Corporation", "weight": 0.078},
        {"ticker": "AAPL", "name": "Apple Inc.", "weight": 0.071},
        {"ticker": "MSFT", "name": "Microsoft Corporation", "weight": 0.065},
    ],
    "QQQ": [
        {"ticker": "NVDA", "name": "NVIDIA Corporation", "weight": 0.089},
        {"ticker": "AAPL", "name": "Apple Inc.", "weight": 0.083},
        {"ticker": "MSFT", "name": "Microsoft Corporation", "weight": 0.077},
    ],
}

def _seed(ticker: str, salt: str = "") -> int:
    h = hashlib.sha256(f"argus-stub:{ticker}:{salt}".encode()).hexdigest()
    return int(h[:8], 16)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def as_of_iso() -> str:
    """Stub as-of: previous weekday 20:00 UTC (pretend last close)."""
    now = datetime.now(timezone.utc)
    d = now - timedelta(days=1)
    while d.weekday() >= 5:  # Sat/Sun -> back to Friday
        d -= timedelta(days=1)
    return d.replace(hour=20, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def get_asset(ticker: str) -> Optional[Dict[str, Any]]:
    return _ASSETS.get(ticker.upper())


def list_assets(
    query: str = "", type_: str = "", sector: str = "", limit: int = 50
) -> List[Dict[str, Any]]:
    out = []
    for a in _ASSETS.values():
        if query and query.lower() not in (
            a["ticker"].lower() + " " + a["name"].lower()
        ):
            continue
        if type_ and a["asset_type"] != type_.upper():
            continue
        if sector and sector.lower() not in a["sector"].lower():
            continue
        out.append(a)
    return out[: max(1, limit)]


def synthetic_prices(ticker: str, n: int = 252) -> List[Dict[str, Any]]:
    """Deterministic synthetic OHLCV — DEMO ONLY, not real market data."""
    a = get_asset(ticker)
    if a is None:
        return []
    rng = np.random.default_rng(_seed(ticker, "prices"))
    # Geometric random walk ending at the stub 'price'.
    rets = rng.normal(0.0004, 0.016, n)
    closes = a["price"] * np.exp(np.cumsum(rets - rets.mean()))
    closes = closes * (a["price"] / closes[-1])
    vols = rng.integers(20_000_000, 90_000_000, n)
    end = datetime.now(timezone.utc).date()
    rows = []
    i = 0
    d = end
    closes_list = closes.tolist()
    # walk backwards over weekdays
    idx = n - 1
    while idx >= 0:
        if d.weekday() < 5:
            c = closes_list[idx]
            o = c * float(1 + rng.normal(0, 0.003))
            hi = max(o, c) * float(1 + abs(rng.normal(0, 0.004)))
            lo = min(o, c) * float(1 - abs(rng.normal(0, 0.004)))
            rows.append(
                {
                    "time": d.isoformat(),
                    "open": round(o, 2),
                    "high": round(hi, 2),
                    "low": round(lo, 2),
                    "close": round(c, 2),
                    "volume": int(vols[idx]),
                }
            )
            idx -= 1
        d -= timedelta(days=1)
        i += 1
        if i > n * 2:
            break
    rows.reverse()
    return rows


def synthetic_fundamentals(ticker: str) -> Dict[str, Any]:
    rng = np.random.default_rng(_seed(ticker, "fundamentals"))
    return {
        "ticker": ticker.upper(),
        "fiscal_period": "2026-06-30",
        "reported_at": as_of_iso(),
        "revenue": round(float(rng.uniform(80e9, 150e9)), 0),
        "eps": round(float(rng.uniform(2.5, 8.0)), 2),
        "ebitda": round(float(rng.uniform(25e9, 60e9)), 0),
        "gross_margin": round(float(rng.uniform(0.35, 0.75)), 3),
        "net_margin": round(float(rng.uniform(0.15, 0.45)), 3),
        "roe": round(float(rng.uniform(0.15, 0.60)), 3),
        "pe": round(float(rng.uniform(20, 60)), 1),
        "forward_pe": round(float(rng.uniform(18, 45)), 1),
        "note": STUB_LABEL,
    }


def synthetic_technicals(ticker: str) -> Dict[str, Any]:
    prices = synthetic_prices(ticker, 120)
    closes = np.array([r["close"] for r in prices], dtype=float)
    # RSI(14)
    delta = np.diff(closes)
    gain = np.clip(delta, 0, None)
    loss = np.clip(-delta, 0, None)
    ag = gain[-14:].mean()
    al = loss[-14:].mean()
    rsi = 100 - 100 / (1 + ag / al) if al > 0 else 100.0
    ema12 = pd_ema(closes, 12)
    ema26 = pd_ema(closes, 26)
    macd = ema12 - ema26
    macd_series = _macd_series(closes)
    valid = macd_series[~np.isnan(macd_series)]
    signal = pd_ema(valid, 9) if len(valid) else float("nan")
    sma50 = closes[-50:].mean()
    sma200 = closes[-200:].mean() if len(closes) >= 200 else float("nan")
    std20 = closes[-20:].std()
    upper = closes[-20:].mean() + 2 * std20
    lower = closes[-20:].mean() - 2 * std20
    tr = np.maximum(
        np.array([r["high"] for r in prices[1:]])
        - np.array([r["low"] for r in prices[1:]]),
        np.abs(
            np.array([r["high"] for r in prices[1:]]) - closes[:-1]
        ),
    )
    atr = tr[-14:].mean()
    return {
        "ticker": ticker.upper(),
        "as_of": as_of_iso(),
        "rsi_14": round(float(rsi), 1),
        "macd": round(float(macd), 3),
        "macd_signal": round(float(signal), 3),
        "sma_50": round(float(sma50), 2),
        "sma_200": round(float(sma200), 2) if not math.isnan(sma200) else None,
        "bollinger_upper": round(float(upper), 2),
        "bollinger_lower": round(float(lower), 2),
        "atr_14": round(float(atr), 2),
        "note": STUB_LABEL,
    }


def pd_ema(x: np.ndarray, span: int) -> float:
    k = 2 / (span + 1)
    e = x[0]
    for v in x[1:]:
        e = v * k + e * (1 - k)
    return float(e)


def _macd_series(closes: np.ndarray) -> np.ndarray:
    out = np.full_like(closes, np.nan, dtype=float)
    for i in range(26, len(closes)):
        out[i] = pd_ema(closes[: i + 1], 12) - pd_ema(closes[: i + 1], 26)
    return out


def synthetic_prediction(ticker: str, horizon: int) -> Dict[str, Any]:
    """Deterministic synthetic per-horizon prediction — DEMO ONLY.

    Clearly NOT a real prediction: data_status=UNCONFIRMED and the values
    come from a seeded RNG, not from the ensemble.
    """
    t = ticker.upper()
    if t not in _ASSETS or horizon not in HORIZONS:
        raise KeyError(f"no stub prediction for {t} h={horizon}")
    rng = np.random.default_rng(_seed(t, f"pred-{horizon}"))
    p_pos = float(np.clip(rng.normal(0.55, 0.10), 0.05, 0.95))
    p_neg = round(1.0 - p_pos, 4)
    p_pos = round(p_pos, 4)
    scale = math.sqrt(horizon / 20)
    exp_ret = round(float(rng.normal(0.03, 0.04)) * scale, 4)
    exp_vol = round(float(abs(rng.normal(0.24, 0.05))) * scale, 4)
    hi = round(exp_ret + 1.28 * exp_vol / math.sqrt(max(horizon, 1)) * 2, 4)
    lo = round(exp_ret - 1.28 * exp_vol / math.sqrt(max(horizon, 1)) * 2, 4)
    ci = [round(lo, 4), round(hi, 4)]
    downside = round(abs(lo) * 0.6, 4)
    upside = round(abs(hi) * 0.8, 4)
    rr = round(upside / downside, 2) if downside > 0 else None
    score = int(np.clip(rng.normal(62, 14), 5, 98))
    conf = int(np.clip(rng.normal(58, 12), 10, 95))
    regimes = ["Bull", "Sideways", "Bear", "HighVol"]
    regime = regimes[_seed(t, "regime") % len(regimes)]
    return {
        "ticker": t,
        "horizon": horizon,
        "as_of": as_of_iso(),
        "model_version": "ARGUS-EQ-1.0",
        "regime": regime,
        "p_positive": p_pos,
        "p_negative": p_neg,
        "expected_return": exp_ret,
        "expected_vol": exp_vol,
        "expected_high": hi,
        "expected_low": lo,
        "ci": ci,
        "downside_risk": downside,
        "upside_potential": upside,
        "risk_reward": rr,
        "composite_score": score,
        "confidence": conf,
        "data_status": DATA_STATUS,
        "explanation": {
            "reasons": [
                "DEMO: synthetic stub — reasons are placeholders, not analysis.",
                f"Seeded momentum proxy is {'positive' if p_pos > 0.5 else 'negative'} for {t}.",
            ],
            "risks": [
                "DEMO: no real risk analysis performed.",
                "Stub data is synthetic; any inference from it is invalid.",
            ],
            "invalidated_if": [
                "Always invalid: this is a synthetic demo record.",
            ],
        },
        "disclaimer": "Probabilistic estimate. Not a guarantee of future performance.",
    }


def etf_profile(ticker: str) -> Optional[Dict[str, Any]]:
    a = get_asset(ticker)
    if a is None or a["asset_type"] != "ETF":
        return None
    return {
        "ticker": a["ticker"],
        "name": a["name"],
        "issuer": a.get("issuer"),
        "expense_ratio": a.get("expense_ratio"),
        "aum": a.get("market_cap"),
        "holdings": _ETF_HOLDINGS.get(a["ticker"], []),
        "sector_exposure": [
            {"sector": "Technology", "weight": 0.42},
            {"sector": "Financials", "weight": 0.13},
            {"sector": "Health Care", "weight": 0.11},
        ],
        "concentration_top10": 0.38,
        "tracking_error": None,
        "note": STUB_LABEL,
    }


def market_regime_stub() -> Dict[str, Any]:
    return {
        "regime": "Sideways",
        "confidence": 0.54,
        "as_of": as_of_iso(),
        "drivers": [
            "DEMO: stub regime — no real regime classification performed.",
            "Seeded volatility proxy near median.",
        ],
        "history": [
            {"date": (datetime.now(timezone.utc) - timedelta(days=i)).date().isoformat(),
             "regime": ["Bull", "Sideways", "Bull", "HighVol", "Sideways"][i % 5]}
            for i in range(5)
        ],
        "note": STUB_LABEL,
    }
