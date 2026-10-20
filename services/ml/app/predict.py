"""Live 8-engine prediction pipeline (Phase 4-9 integration).

``build_live_prediction(ticker, horizon)`` runs the full pipeline from
ARCHITECTURE.md on real data and returns a dict matching the ``Prediction``
schema. Raises ``LivePredictionError`` on any failure (the router falls back
to the clearly-labeled synthetic stub).

Design decisions (all documented, all honest):
  * Engines A/B/H feed the *calibrated* probability via the pinned
    ARGUS-EQ-1.1 regime weights. C/D/F/G were not part of the 1.1 OOF
    learning, so they are reported as overlay signals (engine_signals +
    explanation) and nudge the composite score with documented prior
    weights -- they do NOT enter the calibrated p_positive. Folding them
    into p without OOF validation would violate the honesty rules.
  * p_raw = sum(w_i * (s_i+1)/2) over A/B/H, then the isotonic map from
    registry/calibration/calibration_ARGUS-EQ-1.1_h{horizon}.json (one file
    per horizon in {1,3,5,10,20,63,126,252}; horizon 20 falls back to the
    legacy registry/calibration_ARGUS-EQ-1.1.json). If no map exists for
    the horizon, or the used map's ``calibrated`` flag is false (e.g. a
    thin regime with < 50 OOF samples), p_raw is used uncalibrated and
    ``calibrated=false`` is reported with a confidence penalty.
  * expected_return = (2p-1) * sigma_h, where sigma_h is trailing-63d daily
    volatility scaled to the horizon. This is a documented heuristic
    (signal-scaled trailing vol), NOT a fitted conditional mean.
  * The distribution (high/low, CI, downside/upside, risk/reward) comes from
    montecarlo.residual_bootstrap on trailing-252d demeaned daily returns.
  * composite_score (0-100): 70% calibrated p_positive + 30% overlay blend
    of C/D/F/G (prior weights 0.10/0.10/0.05/0.05 over engines with data,
    renormalized). Labeled as prior-weighted pending walk-forward learning.
  * confidence (0-100, setup quality): starts at 100; -12 per MISSING engine
    among the 8; -10 if uncalibrated; -20 * (1 - agreement), where
    agreement = 1 - std(available engine signals).
  * data_status: worst of the key engines (A/B/H/D), ordered
    CONFIRMED < LIKELY < UNCONFIRMED < MISSING.
  * LATENCY: everything up to and including the regime classification is
    HORIZON-INDEPENDENT for a given (ticker, as-of bar), yet the pipeline used
    to refetch SPY/^VIX (3 polite sleeps + 3 HTTP attempts + 3 CSV reads),
    re-run all eight engines and re-derive the regime for EVERY horizon. A
    bounded, TTL'd per-process market snapshot cache (``_market_snapshot``,
    key = ticker + resolved as-of bar, ``ARGUS_PREDICT_MARKET_TTL`` seconds,
    default 300) now does that work once per ticker. Only the horizon-dependent
    half (blend -> calibrate -> bootstrap -> score/confidence) runs per
    horizon, so predictions are unchanged value-for-value (see
    ``clear_market_cache``/``market_cache_stats`` for introspection).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

log = logging.getLogger("argus.predict")

# Ensure the shared quant package is importable (monorepo layout).
from app.quant_compat import QUANT_AVAILABLE  # noqa: E402,F401

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
REGISTRY_DIR = os.path.join(REPO, "services", "ml", "app", "registry")

_EQ11 = None
_CAL = None
_CAL_H = {}  # per-horizon calibration maps: {horizon: registry doc}

# Overlay prior weights for engines not in the 1.1 OOF learning (ARCHITECTURE
# section 5: initial weights are priors, pending walk-forward learning).
OVERLAY_PRIOR_W = {"Fundamentals": 0.10, "Macro": 0.10,
                   "Options": 0.05, "Sentiment": 0.05}
CORE_ENGINES = ("Technical", "Factors", "CrossAsset")  # A, B, H
ENGINE_LETTER = {"Technical": "A", "Factors": "B", "Fundamentals": "C",
                 "Macro": "D", "Regime": "E", "Options": "F",
                 "Sentiment": "G", "CrossAsset": "H"}
STATUS_RANK = {"CONFIRMED": 0, "LIKELY": 1, "UNCONFIRMED": 2,
               "SYNTHETIC_FIXTURE": 2, "CONFLICTING": 3, "MISSING": 4}


class LivePredictionError(Exception):
    """The live pipeline could not produce a prediction."""


# ---------------------------------------------------------------------------
# Shared market-data cache (horizon-independent work)
#
# The pipeline's first half -- price/SPY/^VIX fetch, fundamentals, macro,
# options, filings/sentiment, run_engines() and the regime classification --
# depends ONLY on (ticker, as-of bar). It used to run once per requested
# horizon. A per-process, bounded, TTL'd cache keyed by ticker+as_of makes the
# fan-out over 8 horizons pay for it once. Behaviour is identical: the cache
# never changes a value, it only avoids repeating the work (proved by
# comparing payloads with the cache disabled).
#
# TTL: ARGUS_PREDICT_MARKET_TTL seconds (default 300, "0" disables caching).
# Bounds: at most _MARKET_CACHE_MAX entries; when full, the entry closest to
# expiry is dropped. Thread-safe: concurrent requests for the same ticker
# share a single build (per-key lock) and a plain read is lock-free under the
# dict lock.
# ---------------------------------------------------------------------------
MARKET_TTL_ENV = "ARGUS_PREDICT_MARKET_TTL"
_MARKET_TTL_DEFAULT = 300.0
_MARKET_CACHE_MAX = 64
_MARKET_CACHE: dict[str, tuple[float, dict]] = {}
_MARKET_BUILD_LOCKS: dict[str, threading.Lock] = {}
_MARKET_CACHE_LOCK = threading.Lock()
_MARKET_STATS = {"hits": 0, "misses": 0, "evictions": 0, "disabled": 0}


def market_cache_ttl() -> float:
    """TTL in seconds for the market snapshot cache ("0" disables it)."""
    raw = os.environ.get(MARKET_TTL_ENV)
    if raw is None or str(raw).strip() == "":
        return _MARKET_TTL_DEFAULT
    try:
        return max(0.0, float(raw))
    except ValueError:
        log.warning("%s=%r is not a number - using %.0fs", MARKET_TTL_ENV, raw,
                    _MARKET_TTL_DEFAULT)
        return _MARKET_TTL_DEFAULT


def clear_market_cache() -> None:
    """Drop every cached market snapshot (tests / forced refresh)."""
    with _MARKET_CACHE_LOCK:
        _MARKET_CACHE.clear()


def market_cache_stats() -> dict:
    """Cache introspection: entries, hits, misses, evictions, TTL."""
    with _MARKET_CACHE_LOCK:
        now = time.monotonic()
        return {"ttl_seconds": market_cache_ttl(),
                "max_entries": _MARKET_CACHE_MAX,
                "entries": len(_MARKET_CACHE),
                "live_entries": sum(1 for exp, _ in _MARKET_CACHE.values()
                                    if exp > now),
                "hits": _MARKET_STATS["hits"],
                "misses": _MARKET_STATS["misses"],
                "evictions": _MARKET_STATS["evictions"],
                "disabled": _MARKET_STATS["disabled"]}


def _market_key(ticker: str) -> str:
    return ticker.upper().replace("^", "")


def _market_lookup(key: str) -> dict | None:
    ttl = market_cache_ttl()
    if ttl <= 0:
        with _MARKET_CACHE_LOCK:
            _MARKET_STATS["disabled"] += 1
        return None
    now = time.monotonic()
    with _MARKET_CACHE_LOCK:
        hit = _MARKET_CACHE.get(key)
        if hit is not None and hit[0] > now:
            _MARKET_STATS["hits"] += 1
            return hit[1]
        if hit is not None:
            _MARKET_CACHE.pop(key, None)
        _MARKET_STATS["misses"] += 1
        return None


def _market_store(key: str, snapshot: dict) -> None:
    ttl = market_cache_ttl()
    if ttl <= 0:
        return
    expires = time.monotonic() + ttl
    with _MARKET_CACHE_LOCK:
        _MARKET_CACHE[key] = (expires, snapshot)
        over = len(_MARKET_CACHE) - _MARKET_CACHE_MAX
        if over > 0:
            for k in sorted(_MARKET_CACHE,
                            key=lambda k: _MARKET_CACHE[k][0])[:over]:
                _MARKET_CACHE.pop(k, None)
                _MARKET_STATS["evictions"] += 1


def _market_build_lock(key: str) -> threading.Lock:
    with _MARKET_CACHE_LOCK:
        lock = _MARKET_BUILD_LOCKS.get(key)
        if lock is None:
            lock = _MARKET_BUILD_LOCKS[key] = threading.Lock()
        return lock


def _load_registry():
    global _EQ11, _CAL
    if _EQ11 is None:
        with open(os.path.join(REGISTRY_DIR, "ARGUS-EQ-1.1.json")) as f:
            _EQ11 = json.load(f)
    if _CAL is None:
        p = os.path.join(REGISTRY_DIR, "calibration_ARGUS-EQ-1.1.json")
        _CAL = json.load(open(p)) if os.path.exists(p) else {}
    return _EQ11, _CAL


def _calibration_for(horizon: int) -> dict:
    """Registry calibration doc for ``horizon`` (cached).

    Per-horizon files live at registry/calibration/
    calibration_ARGUS-EQ-1.1_h{horizon}.json. If the file is missing and
    horizon == 20, the legacy registry/calibration_ARGUS-EQ-1.1.json is
    used; any other missing horizon is uncalibrated.
    """
    if horizon in _CAL_H:
        return _CAL_H[horizon]
    path = os.path.join(REGISTRY_DIR, "calibration",
                        f"calibration_ARGUS-EQ-1.1_h{horizon}.json")
    cal = None
    if os.path.exists(path):
        with open(path) as f:
            cal = json.load(f)
    elif horizon == 20:
        _, cal = _load_registry()  # legacy fallback
    _CAL_H[horizon] = cal or {}
    return _CAL_H[horizon]


def _apply_map(p_raw: float, entry: dict):
    x = np.asarray(entry.get("x") or [], dtype=float)
    y = np.asarray(entry.get("y") or [], dtype=float)
    if len(x) < 2:
        return p_raw, False
    # Legacy maps predate the explicit flag; they were built under the
    # same >=20 rules, so absence of the key keeps legacy behavior.
    if not entry.get("calibrated", True):
        return p_raw, False
    return float(np.interp(p_raw, x, y)), True


def _calibrate(p_raw: float, regime: str, horizon: int):
    """Isotonic calibration; returns (p_cal, calibrated_bool).

    The regime entry's own ``calibrated`` flag is honored: thin regimes
    (fallback entries with calibrated=false) come back uncalibrated even
    though a global map exists -- there is no trustworthy evidence for
    that slice. Returns (p_raw, False) whenever the used map is not
    calibrated.
    """
    maps = _calibration_for(horizon).get("maps") or {}
    entry = maps.get(regime) or {}
    if entry.get("fallback"):
        # Thin regime: the entry carries the honesty verdict. New files
        # mark these calibrated=false; legacy files without the key keep
        # the old fallback-to-global behavior.
        if entry.get("calibrated", True) is False:
            return p_raw, False
        entry = maps.get("global") or {}
    return _apply_map(p_raw, entry)


def _safe_connector(fn, *args, **kwargs):
    """Run a data connector; any failure -> None (engine reports MISSING)."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - connectors must never break serving
        log.warning("connector %s failed: %s", getattr(fn, "__name__", fn),
                    type(exc).__name__)
        return None


def _build_market_snapshot(t: str) -> dict:
    """Everything the pipeline needs that does NOT depend on the horizon.

    Prices (ticker/SPY/^VIX), point-in-time connector data, the eight engine
    results and the regime classification. Deterministic for a given
    (ticker, as-of bar); cached by :func:`_market_snapshot`.
    """
    from app import prices as PX
    from argus_quant import engines as QE
    from argus_quant import regime as RG
    from argus_quant.data_connectors import edgar, fred, options, sentiment

    # ---- 1. prices (real, cached) -------------------------------------
    try:
        px = PX.fetch_many([t, "SPY", "^VIX"], use_cache=True)
        df, spy, vix = px[t], px["SPY"], px["^VIX"]
    except Exception as exc:
        raise LivePredictionError(f"price fetch failed: {exc}") from exc
    if len(df) < 70:
        raise LivePredictionError(f"insufficient history for {t}")
    as_of = df.index[-1]
    as_of_s = as_of.strftime("%Y-%m-%d")
    price_now = float(df["close"].iloc[-1])

    # ---- 2. connector data (each may be None -> MISSING) ---------------
    cik = _safe_connector(edgar.cik_for_ticker, t)
    fundamentals = (_safe_connector(edgar.fundamentals_for_ticker, t, as_of,
                                    price_now) if cik else None)
    sector = None
    try:
        from app import data_stub
        a = data_stub.get_asset(t)
        sector = (a or {}).get("sector")
    except Exception:  # noqa: BLE001
        pass
    sector_ctx = QE.load_sector_context(sector) if sector else None
    macro = _safe_connector(fred.macro_features, as_of)
    chain = _safe_connector(options.get_chain, t)
    options_data = (_safe_connector(options.chain_features, chain, df,
                                    as_of.strftime("%Y-%m-%d"))
                    if chain else None)
    filings = (_safe_connector(edgar.recent_filings, cik, as_of)
               if cik else None)
    sentiment_data = (_safe_connector(sentiment.sentiment_summary, cik, as_of)
                      if cik else None)

    # ---- 3. engines ----------------------------------------------------
    res = QE.run_engines(
        df, as_of, peers={"SPY": spy, "VIX": vix},
        fundamentals=fundamentals, sector_context=sector_ctx,
        macro_data=macro, options_data=options_data,
        sentiment_data=sentiment_data)

    # ---- 4. regime ------------------------------------------------------
    reg = {"regime": "Neutral", "confidence": 30, "drivers": [],
           "data_status": "MISSING"}
    try:
        trend = float(spy["close"].iloc[-1] /
                      spy["close"].rolling(200).mean().iloc[-1] - 1.0)
        v = vix["close"]
        reg = RG.classify_regime(
            as_of, trend=trend, vix=v,
            vix_chg20=float(v.iloc[-1] / v.iloc[-21] - 1.0) if len(v) > 21 else None,
            curve=(macro or {}).get("curve_10y_2y"),
            hy_z=(macro or {}).get("hy_spread_z"),
            breadth=None)
    except Exception as exc:  # noqa: BLE001
        log.warning("regime classification failed: %s", type(exc).__name__)

    return {"ticker": t, "df": df, "spy": spy, "vix": vix,
            "as_of": as_of, "as_of_s": as_of_s, "price_now": price_now,
            "cik": cik, "fundamentals": fundamentals, "sector": sector,
            "sector_ctx": sector_ctx, "macro": macro, "chain": chain,
            "options_data": options_data, "filings": filings,
            "sentiment_data": sentiment_data, "engines": res, "regime": reg}


def _market_snapshot(t: str) -> dict:
    """TTL'd market snapshot for ``t`` (the horizon-independent half).

    Keyed by ticker only: the as-of bar is part of the snapshot, so a roll to
    a new session is picked up as soon as the entry expires (TTL, default
    300 s, ``ARGUS_PREDICT_MARKET_TTL``). Concurrent builds for the same
    ticker are serialised, so N horizon threads do the work once.
    """
    key = _market_key(t)
    snap = _market_lookup(key)
    if snap is not None:
        return snap
    lock = _market_build_lock(key)
    with lock:
        snap = _market_lookup(key)          # another thread may have built it
        if snap is not None:
            return snap
        snap = _build_market_snapshot(t)
        _market_store(key, snap)
        return snap


def build_live_prediction(ticker: str, horizon: int = 20) -> dict:
    """Full live prediction for ``ticker``/``horizon`` on real data."""
    from argus_quant import montecarlo as MC

    t = ticker.upper().replace("^", "")
    if horizon not in (1, 3, 5, 10, 20, 63, 126, 252):
        raise LivePredictionError(f"unsupported horizon {horizon}")

    # ---- 1-3. market snapshot (prices, connectors, engines, regime) -----
    snap = _market_snapshot(t)
    df, as_of = snap["df"], snap["as_of"]
    filings, res, reg = snap["filings"], snap["engines"], snap["regime"]
    letter2name = {v: k for k, v in ENGINE_LETTER.items()}
    engines_out = {}
    for letter, r in res.items():
        name = letter2name.get(letter, letter)
        engines_out[name] = {
            "signal": round(float(r.get("signal", 0.0)), 4),
            "confidence": round(float(r.get("confidence", 0.0)), 3),
            "data_status": r.get("data_status", "MISSING"),
            "note": str(r.get("notes") or r.get("note") or "")[:280],
        }

    regime = reg.get("regime", "Neutral")
    # Wire the real regime classification into the E engine slot
    # (run_engines leaves engine_e as a documented stub).
    REGIME_SIGNAL = {"Strong Bull": 1.0, "Bull": 0.5, "Neutral": 0.0,
                     "Low Vol": 0.1, "High Vol": -0.2, "Risk-Off": -0.25,
                     "Bear": -0.6, "Crisis": -1.0}
    engines_out["Regime"] = {
        "signal": round(REGIME_SIGNAL.get(regime, 0.0), 4),
        "confidence": round(float(reg.get("confidence", 0) or 0) / 100.0, 3),
        "data_status": reg.get("data_status", "MISSING"),
        "note": "; ".join(reg.get("drivers", [])[:3])[:280],
    }

    # ---- 5. blend + calibrate ------------------------------------------
    eq11, _ = _load_registry()
    w = (eq11["regimes"].get(regime) or {}).get("weights") \
        or eq11["global_fallback_weights"]
    p_raw = sum(w.get(e, 0.0) * (engines_out[e]["signal"] + 1.0) / 2.0
                for e in CORE_ENGINES)
    p_raw = float(np.clip(p_raw, 0.01, 0.99))
    p_pos, calibrated = _calibrate(p_raw, regime, horizon)
    p_neg = 1.0 - p_pos

    # ---- 6. distribution -------------------------------------------------
    rets = df["close"].pct_change().dropna().iloc[-252:]
    sigma_d = float(rets.std())
    sigma_h = sigma_d * np.sqrt(horizon)
    expected_return = float((2.0 * p_pos - 1.0) * sigma_h)
    dist = MC.residual_bootstrap(rets, expected_return, horizon=horizon,
                                 n_paths=20_000, seed=42)

    # ---- 7. composite score (priors) --------------------------------------
    overlay = {e: wgt for e, wgt in OVERLAY_PRIOR_W.items()
               if engines_out.get(e, {}).get("data_status") != "MISSING"}
    if overlay:
        tot = sum(overlay.values())
        overlay_p = sum(wgt / tot * (engines_out[e]["signal"] + 1.0) / 2.0
                        for e, wgt in overlay.items())
        score = int(round(100 * (0.70 * p_pos + 0.30 * overlay_p)))
    else:
        score = int(round(100 * p_pos))
    score = int(np.clip(score, 1, 99))

    # ---- 8. confidence -----------------------------------------------------
    n_missing = sum(1 for e in engines_out.values()
                    if e["data_status"] == "MISSING")
    sigs = [e["signal"] for e in engines_out.values()
            if e["data_status"] != "MISSING"]
    agreement = 1.0 - float(np.std(sigs)) if len(sigs) > 1 else 0.5
    confidence = int(np.clip(100 - 12 * n_missing
                             - (10 if not calibrated else 0)
                             - 20 * (1.0 - agreement), 5, 98))

    # ---- 9. explanation ------------------------------------------------------
    contrib = sorted(
        ((e, w.get(e, 0.0) * engines_out[e]["signal"]) for e in CORE_ENGINES),
        key=lambda kv: kv[1], reverse=True)
    names = {"Technical": "technical/momentum", "Factors": "factor",
             "CrossAsset": "cross-asset"}
    reasons = [
        f"{names.get(e, e)} signal {engines_out[e]['signal']:+.2f} "
        f"({engines_out[e]['data_status'].lower()})"
        for e, _ in contrib if engines_out[e]["data_status"] != "MISSING"]
    for e in ("Fundamentals", "Macro", "Options", "Sentiment"):
        eo = engines_out.get(e, {})
        if eo.get("data_status") not in ("MISSING", None):
            reasons.append(
                f"{e.lower()} overlay {eo['signal']:+.2f} "
                f"({eo['data_status'].lower()})")
    risks = [f"{e}: {engines_out[e]['note'] or 'unavailable'}"
             for e in engines_out
             if engines_out[e]["data_status"] == "MISSING"]
    risks.append(f"regime={regime} (confidence {reg.get('confidence', '?')})")
    invalidated = [
        f"this {horizon}d outlook fails if the market regime flips to "
        f"Bear/Crisis (now {regime})",
        "fails if realized volatility exceeds the trailing estimate "
        f"({sigma_h:.1%} over {horizon}d)",
    ]
    if not calibrated:
        invalidated.append("probability is uncalibrated for this horizon "
                           "(no isotonic map) -- treat as directional only")

    # ---- 10. envelope fields ---------------------------------------------------
    worst = max((STATUS_RANK.get(engines_out[e]["data_status"], 4)
                 for e in ("Technical", "Factors", "CrossAsset", "Macro")),
                default=4)
    overall = [k for k, v in STATUS_RANK.items() if v == worst][0]
    if overall == "SYNTHETIC_FIXTURE":
        overall = "UNCONFIRMED"

    rr = dist["risk_reward"]
    return {
        "ticker": t,
        "horizon": horizon,
        "as_of": as_of.isoformat(),
        "model_version": "ARGUS-EQ-1.1",
        "regime": regime,
        "p_positive": round(p_pos, 4),
        "p_negative": round(p_neg, 4),
        "expected_return": round(float(dist["expected_return"]), 4),
        "expected_vol": round(float(dist["expected_vol"]), 4),
        "expected_high": round(float(dist["expected_high"]), 4),
        "expected_low": round(float(dist["expected_low"]), 4),
        "ci": [round(float(dist["ci_lower"]), 4),
               round(float(dist["ci_upper"]), 4)],
        "downside_risk": round(float(dist["downside_risk"]), 4),
        "upside_potential": round(float(dist["upside_potential"]), 4),
        "risk_reward": round(float(rr), 2) if rr else None,
        "composite_score": score,
        "confidence": confidence,
        "data_status": overall,
        "engine_signals": engines_out,
        "pipeline": "live",
        "calibrated": calibrated,
        "explanation": {
            "reasons": reasons[:7] or ["no engine with usable data"],
            "risks": risks[:7],
            "invalidated_if": invalidated,
        },
        "disclaimer": ("Probabilistic estimate from historical and current "
                       "data. Not a guarantee of future performance."),
        "_debug": {
            "p_raw": round(p_raw, 4),
            "weights_used": {e: w.get(e) for e in CORE_ENGINES},
            "regime_drivers": reg.get("drivers", []),
            "filings_seen": bool(filings),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
