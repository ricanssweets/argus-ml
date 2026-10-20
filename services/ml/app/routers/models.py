"""GET /api/v1/models* — model registry / observatory.

REAL DATA ONLY. This router used to invent the observatory numbers with a
seeded numpy Generator keyed on sha256("version|horizon") and draw
Brier/ROC-AUC/calibration-error values inside the published target
thresholds. That code is gone: every metric now comes from a named real
source via :mod:`app.observatory` (Postgres ``model_metrics_daily`` /
``prediction_outcomes`` / ``calibration_maps`` / ``degradation_flags``, or
the shipped ``app/registry`` calibration maps), and when no source has the
number the field is ``null`` with ``data_status="MISSING"`` and a ``notes``
entry saying which sources were consulted and empty.

Governing rule: missing data is REPORTED, never fabricated.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request
from pydantic import Field

from app import data_stub, observatory
from app.ensemble import VERSION as ENSEMBLE_VERSION
from app.routers import env, not_found
from app.schemas import CalibrationCurve, ModelMetrics, ModelVersionInfo

router = APIRouter(tags=["models"])


# ---------------------------------------------------------------------------
# Additive response models (schemas.py is owned by another lane; the extra
# fields below are additive and documented — apps/web/lib/types.ts wants
# brier/roc_auc/cal_error/n on the version row and
# predicted/observed/n on each calibration bucket).
# ---------------------------------------------------------------------------
class ModelVersionRow(ModelVersionInfo):
    brier: Optional[float] = None
    roc_auc: Optional[float] = None
    cal_error: Optional[float] = None
    n: int = 0
    source: str = "MISSING"
    metrics_notes: List[str] = Field(default_factory=list)


class ModelMetricsOut(ModelMetrics):
    source: str = "MISSING"
    notes: List[str] = Field(default_factory=list)
    n_excluded: int = 0
    computed_at: Optional[str] = None


class CalibrationCurveOut(CalibrationCurve):
    source: str = "MISSING"
    regime: str = "global"
    n_oof: Optional[int] = None
    brier_before: Optional[float] = None
    brier_after: Optional[float] = None
    fitted_at: Optional[str] = None
    regimes: List[Dict[str, Any]] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


def _known(version: str) -> bool:
    """Known = pinned in code (ensemble.VERSION) or present in the registry."""
    return version == ENSEMBLE_VERSION or observatory.is_known(version)


def _worst(statuses: List[str]) -> str:
    worst = max((observatory.STATUS_RANK.get(s, 4) for s in statuses),
                default=4)
    for name, rank in observatory.STATUS_RANK.items():
        if rank == worst:
            return name
    return "MISSING"


@router.get("/models")
def list_models(request: Request):
    """One row per version that really exists in the shipped registry.

    ARGUS-EQ-1.0 is the version pinned in ``app.ensemble`` (priors only: the
    registry has no OOF metrics for it, so its metrics are null/MISSING).
    ARGUS-EQ-1.1 is the version ``app.predict`` actually serves; its OOF
    metrics come from the shipped registry file and are labelled as such.
    """
    versions = observatory.known_versions()
    if ENSEMBLE_VERSION not in versions:
        versions = [ENSEMBLE_VERSION] + versions
    rows: List[ModelVersionRow] = []
    for v in versions:
        summary = observatory.model_summary(v)
        priors = observatory.weights_are_priors(v)
        rows.append(ModelVersionRow(
            version=v,
            family="ENSEMBLE",
            status=("CHAMPION" if v == ENSEMBLE_VERSION else "REGISTERED"),
            horizons=observatory.registry_horizons(v) or data_stub.HORIZONS,
            weights_are_priors=True if priors is None else priors,
            brier=summary["brier"], roc_auc=summary["roc_auc"],
            cal_error=summary["cal_error"], n=summary["n"],
            source=summary["source"], metrics_notes=summary["notes"],
            data_status=summary["status"],
        ))
    overall = _worst([r.data_status for r in rows])
    return env([r.model_dump() for r in rows], overall, request,
               as_of=data_stub.utcnow_iso())


@router.get("/models/{version}/metrics")
def model_metrics(
    version: str, request: Request,
    horizon: int = Query(20), group_by: str = Query("regime"),
):
    """Measured observatory metrics — or MISSING with null values.

    Never RNG-derived. Sources in priority order: Postgres
    ``model_metrics_daily`` (nightly measured), then ``prediction_outcomes``
    recomputed honestly with ``argus_quant.metrics``. When neither has data
    for this (version, horizon) every metric is null, n is what the source
    reported (0 when unknown) and data_status is MISSING.
    """
    if not _known(version):
        not_found(f"model version {version} not registered")
    res = observatory.model_metrics(version, horizon, group_by)
    payload = ModelMetricsOut(**res["payload"])
    return env(payload.model_dump(), res["status"], request,
               as_of=res["as_of"])


@router.get("/models/{version}/calibration")
def model_calibration(version: str, request: Request, horizon: int = Query(20)):
    """Shipped / fitted reliability curve — never RNG-derived.

    Source order: realised ``prediction_outcomes`` (real per-bucket n), the
    fitted ``calibration_maps`` row, then the shipped registry map
    ``registry/calibration/calibration_<version>_h<horizon>.json`` (legacy
    ``registry/calibration_<version>.json`` for horizon 20). If no map exists
    for this (version, horizon) the curve is empty and data_status is
    MISSING: an empty curve is honest, an invented one is not.

    Known limitation: the shipped map stores one sample count per regime
    slice, not per knot, so per-point ``n`` is null with
    ``n_status="MISSING"`` rather than invented.
    """
    if not _known(version):
        not_found(f"model version {version} not registered")
    res = dict(observatory.calibration(version, horizon))
    status = res.pop("status")
    payload = CalibrationCurveOut(version=version, horizon=horizon,
                                  data_status=status, **res)
    return env(payload.model_dump(), status, request,
               as_of=res.get("fitted_at"))
