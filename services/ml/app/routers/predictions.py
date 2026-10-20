"""GET/POST /api/v1/predictions* — locked prediction records.

GET /predictions/{ticker} runs the LIVE 8-engine pipeline
(app.predict.build_live_prediction) on real data. If the live pipeline
fails for any reason, the endpoint falls back to the clearly-labeled
synthetic stub (data_status=UNCONFIRMED, pipeline="synthetic_stub") --
never a silent failure, never invented numbers presented as real.

The five UI score bars (trend_strength, momentum_score,
fundamental_score, risk_score, volatility_score) are DERIVED, never
invented: see ``_score_fields`` for the exact per-field formula, the
source engine and the provenance record shipped with every row.

Latency (2026-10 audit: 14 203 ms on GET /predictions): the per-ticker
pipelines used to run serially. They are independent, so they now run in a
bounded thread pool (``_parallel_map``) and every computed payload is
memoized in-process for the documented TTL, mirroring the Redis key scheme
in docs/PERFORMANCE.md (``pred:{ticker}:{horizon}:{asof_date}``). The key
contains the as-of date, so a memo hit can never cross into a new trading
day's locked record.

POST /predictions returns 202 with a job stub (no live compute queue);
poll GET /jobs/{job_id}.

NOTE on ``min_score`` (see ``_observed_score_band``): composite_score is
``100 * (0.70 * p_positive + 0.30 * overlay_p)`` with a calibrated
p_positive of roughly 0.5-0.6, so real records land in the 50-60 band. A
``min_score=70`` filter is therefore empty *by construction, honestly* --
it is not a bug in this router, and the threshold is NOT lowered here.
The dashboard default (apps/web/lib/api.ts:176) is what must change.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, Request

from app import data_stub
from app import cache as pred_cache
from app.routers import aggregate_status, env, not_found
from app.schemas import JobStatus, Prediction, PredictionRequest

log = logging.getLogger("argus.predictions")
router = APIRouter(tags=["predictions"])

_JOBS: Dict[str, Dict] = {}

# ---------------------------------------------------------------------------
# UI score bars — DERIVED from real engine output, documented, never invented
# ---------------------------------------------------------------------------
# apps/web/app/stock/[ticker]/page.tsx renders five ScoreBar(width:value) bars.
# The API previously did not return them at all (width:NaN%). Each field below
# is a MONOTONE, BOUNDED rescaling of a real value that the live pipeline
# already computed. No field is estimated, fitted, tuned or seeded.
#
#   signal -> 0..100 :  50 + 50 * clip(signal, -1, 1)
#                       (50 = neutral, 100 = maximally supportive)
#
# field                source (engine letter)      formula
# trend_strength       Technical (A) signal        50 + 50*clip(s, -1, 1)
# momentum_score       Factors (B) signal          50 + 50*clip(s, -1, 1)
# fundamental_score    Fundamentals (C) signal     50 + 50*clip(s, -1, 1)
# risk_score           mean of the AVAILABLE Macro (D) and CrossAsset (H)
#                      signals -> 50 + 50*clip(mean, -1, 1). Both engines are
#                      documented risk-appetite reads (D: FRED tailwind /
#                      headwind incl. HY spread + curve; H: SPY/VIX cross-asset
#                      risk-off read), so the mean is a risk-on/risk-off score,
#                      not an independent metric.
# volatility_score     the pipeline's own residual bootstrap: annualised
#                      realised vol = expected_vol * sqrt(252 / horizon),
#                      scored as 100 * clip(ann_vol / VOL_CAP, 0, 1) with the
#                      documented reference cap VOL_CAP = 0.80 (80% annualised
#                      = full bar). This is a model-derived distributional
#                      quantity computed from real prices, hence LIKELY, never
#                      CONFIRMED.
#
# When the source is absent or MISSING the field is returned as ``null`` (not
# 0, not 50) together with an explicit reason in ``score_provenance``.
_SCORE_FORMULA = "50 + 50*clip(signal, -1, 1)"
_SCORE_FIELDS = ("trend_strength", "momentum_score", "fundamental_score",
                 "risk_score", "volatility_score")
SCORE_SOURCE_ENGINE = {
    "trend_strength": "Technical",
    "momentum_score": "Factors",
    "fundamental_score": "Fundamentals",
}
RISK_ENGINES = ("Macro", "CrossAsset")
VOL_CAP = 0.80
_SCORE_PROVENANCE_NOTE = (
    "Derived UI bars, not model outputs: each value is a bounded rescaling of "
    "real engine output (see routers/predictions.py::_score_fields). They are "
    "not calibrated and must not be read as probabilities or forecasts."
)


def _rescale(signal: float) -> float:
    """Engine signal in [-1, 1] -> 0..100 (50 = neutral, bounded, monotone)."""
    s = max(-1.0, min(1.0, float(signal)))
    return round(50.0 + 50.0 * s, 1)


def _score_fields(payload: Dict, horizon: Optional[int] = None) -> Dict:
    """Derive the 5 UI bars + their provenance from a prediction payload.

    Honest by construction: a field is populated only when its source engine
    actually produced a non-MISSING signal (or, for volatility, when the real
    bootstrap output is present on a LIVE record). Otherwise it is explicitly
    ``None`` with the reason recorded in ``score_provenance``.
    """
    signals = payload.get("engine_signals") or {}
    pipeline = payload.get("pipeline")
    h = horizon if horizon is not None else payload.get("horizon")
    out: Dict = {}
    prov: Dict = {}

    rescaled = {}
    for field, engine in SCORE_SOURCE_ENGINE.items():
        eng = signals.get(engine) or {}
        status = eng.get("data_status")
        sig = eng.get("signal")
        if eng and status not in (None, "MISSING") and sig is not None:
            out[field] = _rescale(sig)
            rescaled[field] = {"source": f"engine_{engine}",
                               "signal": round(float(sig), 4),
                               "formula": _SCORE_FORMULA,
                               "data_status": status}
        else:
            out[field] = None
            rescaled[field] = {
                "source": f"engine_{engine}",
                "reason": (eng.get("note") or f"{engine} engine unavailable")
                          if eng else f"{engine} engine not reported",
                "data_status": status or "MISSING",
            }

    # risk_score: mean of the available risk-appetite engines (D, H).
    used, statuses = [], []
    for name in RISK_ENGINES:
        eng = signals.get(name) or {}
        if eng and eng.get("data_status") not in (None, "MISSING") \
                and eng.get("signal") is not None:
            used.append((name, float(eng["signal"])))
            statuses.append(eng["data_status"])
    if used:
        mean_sig = sum(s for _, s in used) / len(used)
        out["risk_score"] = _rescale(mean_sig)
        rescaled["risk_score"] = {
            "source": "engine_" + "+".join(n for n, _ in used),
            "signals": {n: round(s, 4) for n, s in used},
            "formula": "50 + 50*clip(mean(signals), -1, 1)` over "
                       "Macro (D) and CrossAsset (H)",
            "data_status": aggregate_status(statuses, "LIKELY"),
        }
    else:
        out["risk_score"] = None
        rescaled["risk_score"] = {
            "source": "engine_Macro+engine_CrossAsset",
            "reason": "both risk-appetite engines (Macro, CrossAsset) "
                      "unavailable for this record",
            "data_status": "MISSING",
        }

    # volatility_score: real residual-bootstrap vol, annualised.
    exp_vol = payload.get("expected_vol")
    if pipeline == "live" and exp_vol is not None and h:
        ann_vol = float(exp_vol) * math.sqrt(252.0 / float(h))
        out["volatility_score"] = round(
            100.0 * min(max(ann_vol / VOL_CAP, 0.0), 1.0), 1)
        rescaled["volatility_score"] = {
            "source": "residual_bootstrap.expected_vol",
            "formula": ("100*clip(ann_vol/0.80, 0, 1), "
                        "ann_vol = expected_vol*sqrt(252/horizon)"),
            "expected_vol": round(float(exp_vol), 4),
            "annualised_vol": round(ann_vol, 4),
            "data_status": "LIKELY",
            "reason": "model-derived distributional vol from real trailing "
                      "returns; not an observed market quantity",
        }
    else:
        out["volatility_score"] = None
        rescaled["volatility_score"] = {
            "source": "residual_bootstrap.expected_vol",
            "reason": ("no live bootstrap output (pipeline="
                       f"{pipeline or 'unknown'})" if pipeline != "live"
                       else "pipeline reported no expected_vol"),
            "data_status": "MISSING",
        }

    if pipeline == "synthetic_stub" or not signals:
        # Nothing real to derive from: keep every field null and say why.
        for field in _SCORE_FIELDS:
            out[field] = None
            if field != "volatility_score":
                rescaled[field] = {
                    "source": f"engine_{SCORE_SOURCE_ENGINE.get(field, RISK_ENGINES[0])}",
                    "reason": "record has no live engine_signals "
                              f"(pipeline={pipeline or 'unknown'})",
                    "data_status": "UNCONFIRMED",
                }
    rescaled["note"] = _SCORE_PROVENANCE_NOTE
    out["score_provenance"] = rescaled
    return out


def _with_scores(payload: Dict, horizon: Optional[int] = None) -> Dict:
    """Payload + score fields, recomputing only when they are absent.

    Cached (Redis) payloads written before these fields existed are upgraded
    on read instead of being served with missing keys.
    """
    if any(f not in payload for f in _SCORE_FIELDS):
        payload.update(_score_fields(payload, horizon))
    return payload


# ---------------------------------------------------------------------------
# Latency: in-process TTL memo (mirrors the optional Redis cache)
# ---------------------------------------------------------------------------
# Redis is optional (harness/CI and single-replica deploys run without it),
# so the same ``pred:{ticker}:{horizon}:{asof_date}`` key is also memoized
# per process for the same TTL. The as-of date is part of the key, so a hit
# can never serve a previous session's locked record, and entries expire.
_MEMO: Dict[str, Tuple[float, Dict]] = {}
_MEMO_LOCK = threading.Lock()
_MEMO_MAX = 512


def _memo_ttl() -> int:
    return pred_cache.cache_ttl()


def _memo_get(key: str) -> Optional[Dict]:
    if _memo_ttl() <= 0:
        return None
    with _MEMO_LOCK:
        hit = _MEMO.get(key)
        if hit is not None and hit[0] <= time.monotonic():
            _MEMO.pop(key, None)
            hit = None
    return hit[1] if hit is not None else None


def _memo_set(key: str, payload: Dict) -> None:
    ttl = _memo_ttl()
    if ttl <= 0:
        return
    with _MEMO_LOCK:
        _MEMO[key] = (time.monotonic() + ttl, payload)
        if len(_MEMO) > _MEMO_MAX:  # bounded: drop the soonest-to-expire
            for k in sorted(_MEMO, key=lambda k: _MEMO[k][0])[:len(_MEMO) - _MEMO_MAX]:
                _MEMO.pop(k, None)


def _prediction_workers(n_items: int) -> int:
    """Bounded fan-out; overridable for capacity planning."""
    try:
        cap = int(os.environ.get("PRED_MAX_WORKERS", "4"))
    except ValueError:
        cap = 4
    return max(1, min(cap, max(1, n_items)))


def _parallel_map(fn, items: List) -> List:
    """Run ``fn`` over independent items concurrently, preserving order.

    Each item is a ticker (or a horizon) whose pipeline is fully independent;
    the pool is bounded so a request can never fan out unboundedly.
    """
    if not items:
        return []
    if len(items) == 1:
        return [fn(items[0])]
    with ThreadPoolExecutor(max_workers=_prediction_workers(len(items)),
                            thread_name_prefix="pred") as ex:
        return list(ex.map(fn, items))


def _observed_score_band(horizon: int) -> Dict:
    """Documented reachable composite_score facts (no computation here).

    Kept as data so the diagnosis travels with the code: the calibrated
    probabilities driving composite_score sit near 0.5, so real rows land in
    the 50-60 band; a 70 threshold is empty by construction.
    """
    return {
        "composite_score_formula": "100*(0.70*p_positive + 0.30*overlay_p)",
        "neutral_point": 50,
        "observed_band": [50, 60],
        "observed_note": ("measured 2026-10-15 on the 4-ticker stub universe at "
                          "horizon=20: composite_score 52-57, p_positive "
                          "0.5577-0.5909"),
    }


def _live_prediction(ticker: str, horizon: int) -> Prediction:
    """Live pipeline; raises on failure so the caller can fall back."""
    from app import predict as live
    from app import db as _db

    payload = live.build_live_prediction(ticker, horizon)
    # Persist the locked record (append-only; best-effort — a DB failure
    # never breaks serving, and a missing DATABASE_URL only logs).
    _db.insert_prediction(payload)
    payload.pop("_debug", None)
    payload.update(_score_fields(payload, horizon))
    return Prediction(**payload)


def _prediction_or_404(ticker: str, horizon: int) -> Prediction:
    t = ticker.upper()
    # Live pipeline first (real data, calibrated).
    try:
        return _live_prediction(t, horizon)
    except Exception as exc:  # noqa: BLE001 - fall back loudly, never silently
        log.warning("live pipeline failed for %s h=%s: %s: %s -- stub fallback",
                    t, horizon, type(exc).__name__, exc)
    if data_stub.get_asset(t) is None:
        not_found(f"asset {t} not in stub universe")
    if horizon not in data_stub.HORIZONS:
        not_found(f"horizon {horizon} not in {data_stub.HORIZONS}")
    p = data_stub.synthetic_prediction(t, horizon)
    p["pipeline"] = "synthetic_stub"
    p["calibrated"] = False
    # Synthetic record: the 5 UI bars are null with explicit reasons.
    p.update(_score_fields(p, horizon))
    return Prediction(**p)


def _cached_prediction(ticker: str, horizon: int, asof_date: str) -> Dict:
    """Prediction payload via the optional Redis cache (graceful on miss).

    Key scheme ``pred:{ticker}:{horizon}:{asof_date}`` — see
    docs/PERFORMANCE.md. With the real DB layer, ``asof_date`` becomes the
    locked prediction's as-of date so a cache entry can never serve a stale
    locked record. When Redis is absent (the normal single-replica case) the
    same key is served from the in-process TTL memo instead.
    """
    ttl = pred_cache.cache_ttl()
    key = pred_cache.prediction_key(ticker, horizon, asof_date)
    if ttl > 0:
        hit = pred_cache.cache_get(key)
        if hit is not None:
            return _with_scores(hit, horizon)
        memo = _memo_get(key)
        if memo is not None:
            return _with_scores(memo, horizon)
    payload = _prediction_or_404(ticker, horizon).model_dump()
    if ttl > 0:
        _memo_set(key, payload)
        pred_cache.cache_set(key, payload, ttl)
    return payload


def _parse_horizons(raw: str) -> List[int]:
    """Comma-separated horizons -> ints, with a 422 envelope (not a 500)."""
    hs: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hs.append(int(part))
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"horizons must be comma-separated integers, got {part!r}")
    if not hs:
        raise HTTPException(status_code=422, detail="horizons must not be empty")
    return hs


@router.get("/predictions/{ticker}")
def predictions_for_ticker(
    ticker: str,
    request: Request,
    horizons: str = Query("1,5,20,63", description="comma-separated horizons"),
):
    hs = _parse_horizons(horizons)
    asof_date = data_stub.as_of_iso()[:10]
    preds = _parallel_map(
        lambda h: _cached_prediction(ticker, h, asof_date), hs)
    as_of = preds[0]["as_of"] if preds else data_stub.as_of_iso()
    # meta.data_status is the worst status among the items -- never a blanket
    # UNCONFIRMED (which contradicted CONFIRMED rows) and never better than
    # the data (see routers/__init__.py::aggregate_status).
    status = aggregate_status([p.get("data_status") for p in preds])
    return env(preds, status, request, as_of=as_of)


@router.get("/predictions")
def predictions_filtered(
    request: Request,
    min_score: int = Query(0, ge=0, le=100),
    min_p_positive: float = Query(0.0, ge=0.0, le=1.0),
    horizon: int = Query(20),
):
    if horizon not in data_stub.HORIZONS:
        not_found(f"horizon {horizon} not in {data_stub.HORIZONS}")
    asof_date = data_stub.as_of_iso()[:10]
    tickers = [a["ticker"] for a in data_stub.list_assets(limit=200)]

    def one(t: str) -> Optional[Dict]:
        try:
            return _cached_prediction(t, horizon, asof_date)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad ticker != 500
            log.warning("prediction failed for %s h=%s: %s: %s",
                        t, horizon, type(exc).__name__, exc)
            return None

    rows = [r for r in _parallel_map(one, tickers) if r is not None]
    rows = [r for r in rows
            if r["composite_score"] >= min_score
            and r["p_positive"] >= min_p_positive]
    rows.sort(key=lambda r: r["composite_score"], reverse=True)
    scores = [r["composite_score"] for r in rows]
    status = aggregate_status([r.get("data_status") for r in rows])
    return env(
        rows, status, request, as_of=data_stub.as_of_iso(),
        extra_meta={
            # Additive diagnostics so an empty table is explainable without
            # guessing: the applied filter + what the universe actually
            # produced. Previously the caller got [] with no signal at all.
            "filters": {
                "horizon": horizon,
                "min_score": min_score,
                "min_p_positive": min_p_positive,
                "universe": len(tickers),
                "returned": len(rows),
                "composite_score_range": [min(scores), max(scores)]
                                         if scores else None,
                "filter_effect": _observed_score_band(horizon),
            },
        })


@router.post("/predictions", status_code=202)
def request_prediction(body: PredictionRequest, request: Request):
    t = body.ticker.upper()
    if data_stub.get_asset(t) is None:
        not_found(f"asset {t} not in stub universe")
    job_id = uuid.uuid4().hex[:12]
    # Phase 0: no compute queue — materialize stub predictions immediately
    # and mark the job done. Clearly labeled demo.
    result = [
        _prediction_or_404(t, h).model_dump()
        for h in body.horizons
        if h in data_stub.HORIZONS
    ]
    job = JobStatus(
        job_id=job_id, status="done", ticker=t,
        horizons=[h for h in body.horizons if h in data_stub.HORIZONS],
        result=result,
        # Honest aggregate of the result rows instead of a blanket label.
        data_status=aggregate_status([r.get("data_status") for r in result]),
    )
    _JOBS[job_id] = job.model_dump()
    return env(job.model_dump(), job.data_status, request)


@router.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    job = _JOBS.get(job_id)
    if job is None:
        not_found(f"job {job_id} not found")
    return env(job, job.get("data_status", "UNCONFIRMED"), request)
