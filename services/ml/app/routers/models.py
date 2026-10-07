"""GET /api/v1/models* — model registry / observatory (stub, UNCONFIRMED demo)."""

from __future__ import annotations

import hashlib

import numpy as np
from fastapi import APIRouter, Query, Request

from app import data_stub
from app.ensemble import VERSION as ENSEMBLE_VERSION, ArgusEnsemble
from app.routers import env, not_found
from app.schemas import CalibrationCurve, ModelMetrics, ModelVersionInfo

router = APIRouter(tags=["models"])


def _known(version: str) -> bool:
    return version == ENSEMBLE_VERSION


def _rng(*parts: str) -> np.random.Generator:
    seed = int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)
    return np.random.default_rng(seed)


@router.get("/models")
def list_models(request: Request):
    ens = ArgusEnsemble()
    info = ModelVersionInfo(
        version=ENSEMBLE_VERSION, family="ENSEMBLE", status="CHAMPION",
        horizons=data_stub.HORIZONS, weights_are_priors=True,
        data_status="UNCONFIRMED",
    )
    return env([info.model_dump()], "UNCONFIRMED", request)


@router.get("/models/{version}/metrics")
def model_metrics(
    version: str, request: Request,
    horizon: int = Query(20), group_by: str = Query("regime"),
):
    if not _known(version):
        not_found(f"model version {version} not registered")
    # DEMO: synthetic observatory numbers — clearly labeled, never a claim.
    rng = _rng(version, str(horizon))
    groups = []
    for regime in ["Bull", "Bear", "Sideways", "HighVol"]:
        groups.append({
            "regime": regime, "n": int(rng.integers(25, 200)),
            "brier": round(float(rng.uniform(0.20, 0.26)), 4),
            "roc_auc": round(float(rng.uniform(0.52, 0.60)), 4),
            "calibration_error": round(float(rng.uniform(0.02, 0.06)), 4),
        })
    payload = ModelMetrics(
        version=version, horizon=horizon, group_by=group_by,
        n=sum(g["n"] for g in groups),
        brier=round(float(np.mean([g["brier"] for g in groups])), 4),
        roc_auc=round(float(np.mean([g["roc_auc"] for g in groups])), 4),
        calibration_error=round(float(np.mean([g["calibration_error"] for g in groups])), 4),
        by_group=groups, degradation_flags=[],
        data_status="UNCONFIRMED",
    )
    return env(payload.model_dump(), "UNCONFIRMED", request)


@router.get("/models/{version}/calibration")
def model_calibration(version: str, request: Request, horizon: int = Query(20)):
    if not _known(version):
        not_found(f"model version {version} not registered")
    rng = _rng("cal", version, str(horizon))
    buckets = []
    for i in range(10):
        lo, hi = i / 10, (i + 1) / 10
        n = int(rng.integers(20, 120))  # MIN_SAMPLES_PER_BUCKET=20 respected
        buckets.append({
            "bin": [lo, hi], "n": n,
            "mean_predicted": round((lo + hi) / 2, 3),
            "observed_rate": round(float(np.clip(rng.normal((lo + hi) / 2, 0.05), 0, 1)), 3),
        })
    payload = CalibrationCurve(
        version=version, horizon=horizon, method="ISOTONIC",
        buckets=buckets, min_samples=20, data_status="UNCONFIRMED",
    )
    return env(payload.model_dump(), "UNCONFIRMED", request)
