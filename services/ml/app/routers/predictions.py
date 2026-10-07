"""GET/POST /api/v1/predictions* — locked prediction records.

GET /predictions/{ticker} runs the LIVE 8-engine pipeline
(app.predict.build_live_prediction) on real data. If the live pipeline
fails for any reason, the endpoint falls back to the clearly-labeled
synthetic stub (data_status=UNCONFIRMED, pipeline="synthetic_stub") --
never a silent failure, never invented numbers presented as real.

POST /predictions returns 202 with a job stub (no live compute queue);
poll GET /jobs/{job_id}.
"""

from __future__ import annotations

import logging
import uuid
from typing import Dict, List

from fastapi import APIRouter, Query, Request

from app import data_stub
from app import cache as pred_cache
from app.routers import env, not_found
from app.schemas import JobStatus, Prediction, PredictionRequest

log = logging.getLogger("argus.predictions")
router = APIRouter(tags=["predictions"])

_JOBS: Dict[str, Dict] = {}


def _live_prediction(ticker: str, horizon: int) -> Prediction:
    """Live pipeline; raises on failure so the caller can fall back."""
    from app import predict as live
    from app import db as _db

    payload = live.build_live_prediction(ticker, horizon)
    # Persist the locked record (append-only; best-effort — a DB failure
    # never breaks serving, and a missing DATABASE_URL only logs).
    _db.insert_prediction(payload)
    payload.pop("_debug", None)
    return Prediction(**payload)


def _prediction_or_404(ticker: str, horizon: int) -> Prediction:
    t = ticker.upper()
    # Live pipeline first (real data, calibrated).
    try:
        return _live_prediction(t, horizon)
    except Exception as exc:  # noqa: BLE001 - fall back loudly, never silently
        log.warning("live pipeline failed for %s h=%s: %s -- stub fallback",
                    t, horizon, type(exc).__name__)
    if data_stub.get_asset(t) is None:
        not_found(f"asset {t} not in stub universe")
    if horizon not in data_stub.HORIZONS:
        not_found(f"horizon {horizon} not in {data_stub.HORIZONS}")
    p = data_stub.synthetic_prediction(t, horizon)
    p["pipeline"] = "synthetic_stub"
    p["calibrated"] = False
    return Prediction(**p)


def _cached_prediction(ticker: str, horizon: int, asof_date: str) -> Dict:
    """Prediction payload via the optional Redis cache (graceful on miss).

    Key scheme ``pred:{ticker}:{horizon}:{asof_date}`` — see
    docs/PERFORMANCE.md. With the real DB layer, ``asof_date`` becomes the
    locked prediction's as-of date so a cache entry can never serve a stale
    locked record.
    """
    ttl = pred_cache.cache_ttl()
    key = pred_cache.prediction_key(ticker, horizon, asof_date)
    if ttl > 0:
        hit = pred_cache.cache_get(key)
        if hit is not None:
            return hit
    payload = _prediction_or_404(ticker, horizon).model_dump()
    if ttl > 0:
        pred_cache.cache_set(key, payload, ttl)
    return payload


@router.get("/predictions/{ticker}")
def predictions_for_ticker(
    ticker: str,
    request: Request,
    horizons: str = Query("1,5,20,63", description="comma-separated horizons"),
):
    hs: List[int] = []
    for part in horizons.split(","):
        part = part.strip()
        if part:
            hs.append(int(part))
    preds = [_cached_prediction(ticker, h, data_stub.as_of_iso()[:10]) for h in hs]
    as_of = preds[0]["as_of"] if preds else data_stub.as_of_iso()
    return env(preds, "UNCONFIRMED", request, as_of=as_of)


@router.get("/predictions")
def predictions_filtered(
    request: Request,
    min_score: int = Query(0, ge=0, le=100),
    min_p_positive: float = Query(0.0, ge=0.0, le=1.0),
    horizon: int = Query(20),
):
    rows = []
    for t in data_stub._ASSETS:
        try:
            p = _prediction_or_404(t, horizon)
        except Exception:
            continue
        if p.composite_score >= min_score and p.p_positive >= min_p_positive:
            rows.append(p.model_dump())
    rows.sort(key=lambda r: r["composite_score"], reverse=True)
    return env(rows, "UNCONFIRMED", request, as_of=data_stub.as_of_iso())


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
        result=result, data_status="UNCONFIRMED",
    )
    _JOBS[job_id] = job.model_dump()
    return env(job.model_dump(), "UNCONFIRMED", request)


@router.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    job = _JOBS.get(job_id)
    if job is None:
        not_found(f"job {job_id} not found")
    return env(job, job.get("data_status", "UNCONFIRMED"), request)
