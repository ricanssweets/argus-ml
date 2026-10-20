"""Real Data Layer for the model observatory. NO synthesized numbers.

Governing rule of this codebase: missing data is REPORTED
(``data_status``), never fabricated. This module exists because the previous
``/models/*`` implementation generated Brier / ROC-AUC / calibration numbers
with a seeded numpy Generator keyed on sha256(version|horizon) drawn
inside the published target thresholds. Those numbers were invented. Nothing
in this module is generated: every value either comes from a named real
source or comes back as ``None`` with ``data_status="MISSING"``.

Sources, in priority order, each one named in the response's ``source``:

 1. Postgres via :mod:`app.db` (``db.fetchall``/``fetchone``):
      * ``model_metrics_daily``   — nightly measured observatory metrics
      * ``prediction_outcomes`` JOIN ``predictions`` — realised outcomes
        (Brier/ROC-AUC/calibration recomputed honestly with
        :mod:`argus_quant.metrics`)
      * ``calibration_maps``      — the fitted (isotonic/Platt) map
      * ``degradation_flags``     — WATCH/WARN/CRITICAL events
 2. The shipped registry JSON under ``app/registry`` — real out-of-fold
    metrics and real isotonic maps written by the training pipeline:
      * ``calibration/calibration_<version>_h<horizon>.json``
      * legacy ``calibration_<version>.json`` (horizon 20 only, same rule as
        ``app.predict``)
      * ``<version>.json`` (global OOF block, when the registry has one)
 3. Nothing. Every metric is ``None``, ``n`` is 0, ``data_status`` is
    ``"MISSING"`` and ``notes`` says which sources were consulted and empty.

Known limitation (reported, never papered over): the shipped isotonic maps
store one sample count per *regime slice* (``n_oof``/``n``) and the knot
values (x, y); they do NOT store a per-knot sample count. Per-knot ``n`` is
therefore returned as ``None`` with ``n_status="MISSING"`` — inventing a
count there would be exactly the fabrication this module removes.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from app import db

log = logging.getLogger("argus.ml.observatory")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
REGISTRY_DIR = os.path.join(REPO, "services", "ml", "app", "registry")
CALIBRATION_DIR = os.path.join(REGISTRY_DIR, "calibration")

# Minimum samples for any performance claim (argus_quant.metrics law).
MIN_SAMPLES = 20

STATUS_RANK = {"CONFIRMED": 0, "LIKELY": 1, "UNCONFIRMED": 2,
               "SYNTHETIC_FIXTURE": 2, "CONFLICTING": 3, "MISSING": 4}

_VERSION_FILE = re.compile(r"^ARGUS-EQ-[\d.]+\.json$")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _rel(path: str) -> str:
    """Repo-relative path (stable, auditable source string)."""
    try:
        return os.path.relpath(path, REPO).replace("\\", "/")
    except ValueError:  # pragma: no cover - different drive on Windows
        return path


def _num(x) -> Optional[float]:
    """JSON-safe float: None / NaN / inf -> None."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _round(x, nd: int = 6) -> Optional[float]:
    f = _num(x)
    return None if f is None else round(f, nd)


def _mean(vals: List[Optional[float]]) -> Optional[float]:
    xs = [v for v in (_num(v) for v in vals) if v is not None]
    return round(sum(xs) / len(xs), 6) if xs else None


def _n_weighted(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    """n-weighted mean of ``key`` over rows (None when no usable value).

    This is explicitly *not* a pooled statistic: it is the n-weighted mean of
    the per-group values the source reports (documented in ``notes``).
    """
    num = den = 0.0
    for r in rows:
        v = _num(r.get(key))
        n = _num(r.get("n"))
        if v is None or not n:
            continue
        num += v * n
        den += n
    return round(num / den, 6) if den else None


def _worst_status(statuses) -> str:
    worst = -1
    for s in statuses:
        r = STATUS_RANK.get(str(s), 4) if s else 4
        worst = max(worst, r)
    if worst < 0:
        return "UNCONFIRMED"
    for k, v in STATUS_RANK.items():
        if v == worst:
            return k
    return "UNCONFIRMED"


def _day_str(day) -> Optional[str]:
    if day is None:
        return None
    return str(day)[:10]


# ---------------------------------------------------------------------------
# registry (shipped real files)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=32)
def _load_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def known_versions() -> List[str]:
    """Model versions that actually exist in the shipped registry."""
    try:
        names = os.listdir(REGISTRY_DIR)
    except OSError:
        return []
    out = []
    for name in names:
        if _VERSION_FILE.match(name):
            out.append(name[: -len(".json")])
    return sorted(out)


def is_known(version: str) -> bool:
    return os.path.exists(os.path.join(REGISTRY_DIR, f"{version}.json"))


def read_registry(version: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(REGISTRY_DIR, f"{version}.json")
    if not os.path.exists(path):
        return None
    try:
        return _load_json(path)
    except Exception as exc:  # noqa: BLE001 - a bad file must not break serving
        log.warning("registry %s unreadable: %s", path, type(exc).__name__)
        return None


def registry_horizons(version: str) -> List[int]:
    doc = read_registry(version) or {}
    hs = doc.get("horizons")
    if isinstance(hs, dict):
        return sorted(int(k) for k in hs)
    if isinstance(hs, list):
        return [int(h) for h in hs]
    return []


def registry_oof(version: str) -> Dict[str, Any]:
    """Real out-of-fold metrics documented in ``<version>.json``.

    Returns ``{"brier", "roc_auc", "n", "source"}``; values are None when the
    registry carries no OOF block (e.g. ARGUS-EQ-1.0 is priors-only, every
    ``n_oos_samples`` is 0).
    """
    path = os.path.join(REGISTRY_DIR, f"{version}.json")
    doc = read_registry(version)
    if not doc:
        return {"brier": None, "roc_auc": None, "n": 0,
                "source": f"MISSING: no shipped registry file for {version}"}
    n = doc.get("global_n_samples")
    brier = _num(doc.get("global_oof_brier"))
    auc = _num(doc.get("global_oof_auc"))
    if n is None and brier is None and auc is None:
        return {"brier": None, "roc_auc": None, "n": 0,
                "source": f"{_rel(path)} (registry has no OOF metrics block; "
                          f"file status: {doc.get('status')!r})"}
    return {"brier": brier, "roc_auc": auc, "n": int(n or 0),
            "source": f"{_rel(path)} (global_oof_brier/global_oof_auc, "
                      f"n_samples={n})"}


def _calibration_doc_path(version: str, horizon: int) -> Optional[str]:
    path = os.path.join(CALIBRATION_DIR,
                        f"calibration_{version}_h{horizon}.json")
    if os.path.exists(path):
        return path
    if horizon == 20:  # legacy fallback, same rule as app.predict
        legacy = os.path.join(REGISTRY_DIR, f"calibration_{version}.json")
        if os.path.exists(legacy):
            return legacy
    return None


def _knots_to_buckets(xs, ys, source: str) -> List[Dict[str, Any]]:
    """Isotonic map knots as the reliability curve.

    ``x`` = mean predicted probability of the fitted block, ``y`` = realised
    frequency in that block (that is what an isotonic OOF fit maps to). The
    per-block sample count is NOT in the shipped file -> ``n`` is None with
    ``n_status="MISSING"``.
    """
    n = min(len(xs or []), len(ys or []))
    out: List[Dict[str, Any]] = []
    for i in range(n):
        lo = _num(xs[i])
        hi = _num(xs[i + 1]) if i + 1 < n else lo
        out.append({
            "bin": [lo, hi],
            "predicted": lo,          # web shape (types.ts CalibrationBucket)
            "observed": _num(ys[i]),  # web shape
            "n": None,                # unknown per knot - never invented
            "n_status": "MISSING",
            "mean_predicted": lo,     # legacy shape kept for compatibility
            "observed_rate": _num(ys[i]),
            "source": source,
        })
    return out


def _regime_summary(maps: Dict[str, Any], source: str) -> List[Dict[str, Any]]:
    """Real per-regime sample counts / Brier from the shipped map (no knots)."""
    out = []
    for regime, entry in (maps or {}).items():
        if not isinstance(entry, dict):
            continue
        out.append({
            "regime": regime,
            "n": entry.get("n"),
            "calibrated": entry.get("calibrated"),
            "fallback": bool(entry.get("fallback")),
            "brier_before": _num(entry.get("brier_before")),
            "brier_after": _num(entry.get("brier_after")),
            "knots": min(len(entry.get("x") or []), len(entry.get("y") or [])),
            "reason": entry.get("reason") or "",
            "source": source,
        })
    return out


# ---------------------------------------------------------------------------
# /models/{version}/metrics
# ---------------------------------------------------------------------------

def _empty_metrics(version: str, horizon: int, group_by: str,
                   notes: List[str], n: int = 0,
                   as_of: Optional[str] = None) -> Dict[str, Any]:
    return {
        "payload": {
            "version": version, "horizon": horizon, "group_by": group_by,
            "n": int(n), "brier": None, "roc_auc": None,
            "calibration_error": None, "by_group": [],
            "degradation_flags": [],
            "data_status": "MISSING",
            "source": "MISSING: " + "; ".join(notes),
            "notes": notes,
            "n_excluded": 0,
            "computed_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
        },
        "status": "MISSING",
        "as_of": as_of,
    }


def _from_model_metrics_daily(version: str, horizon: int,
                             group_by: str) -> Optional[Dict[str, Any]]:
    """Measured metrics from the production observatory table."""
    rows = db.fetchall(
        "SELECT day, regime, sector, n, roc_auc, brier, cal_error "
        "FROM model_metrics_daily "
        "WHERE model_version = ? AND horizon = ? "
        "ORDER BY day DESC LIMIT 1000",
        (version, horizon))
    if not rows:
        return None
    day = rows[0].get("day")
    same_day = [r for r in rows if r.get("day") == day]
    dim = group_by if group_by in ("regime", "sector") else None
    groups: Dict[str, List[Dict[str, Any]]] = {}
    if dim:
        for r in same_day:
            groups.setdefault(str(r.get(dim) or "UNKNOWN"), []).append(r)
    else:
        groups["ALL"] = same_day

    by_group = []
    total_n = 0
    for name, rs in groups.items():
        g_n = int(sum(int(_num(r.get("n")) or 0) for r in rs))
        total_n += g_n
        by_group.append({
            dim or "group": name,
            "day": _day_str(rs[0].get("day")),
            "n": g_n,
            "brier": _n_weighted(rs, "brier"),
            "roc_auc": _n_weighted(rs, "roc_auc"),
            "calibration_error": _n_weighted(rs, "cal_error"),
            "rows": len(rs),
        })
    notes = [
        "source: postgres model_metrics_daily (nightly measured metrics)",
        f"day={_day_str(day)}",
        "aggregate is the n-weighted mean of the per-group values the source "
        "reports, not a pooled re-computation",
        "model_metrics_daily has no provenance column -> data_status=LIKELY",
    ]
    payload = {
        "version": version, "horizon": horizon, "group_by": group_by,
        "n": total_n,
        "brier": _n_weighted(same_day, "brier"),
        "roc_auc": _n_weighted(same_day, "roc_auc"),
        "calibration_error": _n_weighted(same_day, "cal_error"),
        "by_group": by_group,
        "degradation_flags": degradation_flags(version, horizon),
        "data_status": "LIKELY",
        "source": "postgres:model_metrics_daily",
        "notes": notes,
        "n_excluded": 0,
        "computed_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
    }
    if total_n < MIN_SAMPLES:
        payload.update({"brier": None, "roc_auc": None,
                        "calibration_error": None, "data_status": "MISSING",
                        "source": "MISSING: postgres:model_metrics_daily"
                                  f" n={total_n} < {MIN_SAMPLES}"})
        payload["notes"] = notes + [
            f"n={total_n} < MIN_SAMPLES_PER_BUCKET={MIN_SAMPLES}: no "
            f"performance claim is made"]
        return {"payload": payload, "status": "MISSING",
                "as_of": _day_str(day)}
    return {"payload": payload, "status": "LIKELY", "as_of": _day_str(day)}


def _from_prediction_outcomes(version: str, horizon: int,
                              group_by: str) -> Optional[Dict[str, Any]]:
    """Metrics recomputed from realised outcomes (honest formulas)."""
    rows = db.fetchall(
        "SELECT p.p_positive, p.regime, p.data_status_overall, o.actual_return "
        "FROM prediction_outcomes o "
        "JOIN predictions p ON p.id = o.prediction_id "
        "WHERE p.model_version = ? AND p.horizon = ? "
        "AND o.actual_return IS NOT NULL LIMIT 20000",
        (version, horizon))
    if not rows:
        return None
    usable = [r for r in rows
              if _num(r.get("p_positive")) is not None
              and _num(r.get("actual_return")) is not None]
    excluded = len(rows) - len(usable)
    if not usable:
        return None
    y = [1.0 if _num(r["actual_return"]) > 0 else 0.0 for r in usable]
    p = [_num(r["p_positive"]) for r in usable]
    src = "postgres:prediction_outcomes JOIN predictions"
    notes = [
        "source: " + src,
        "Brier = mean((p_positive - 1{actual_return>0})^2); ROC-AUC and the "
        "calibration error via argus_quant.metrics (calibration_table, "
        f"MIN_SAMPLES_PER_BUCKET={MIN_SAMPLES})",
        f"excluded {excluded} outcome row(s) without p_positive/actual_return",
        "data_status = worst provenance of the source prediction rows",
    ]
    if len(usable) < MIN_SAMPLES:
        notes.append(f"n={len(usable)} < MIN_SAMPLES_PER_BUCKET="
                     f"{MIN_SAMPLES}: realised metrics reported as MISSING, "
                     f"not as a claim")
        out = _empty_metrics(version, horizon, group_by, notes,
                             n=len(usable))
        out["payload"]["n_excluded"] = excluded
        out["payload"]["source"] = f"MISSING: {src} n={len(usable)}"
        return out

    try:
        from argus_quant import metrics as QM
    except Exception as exc:  # noqa: BLE001
        notes.append(f"argus_quant.metrics unavailable ({type(exc).__name__})"
                     " - cannot compute honestly")
        out = _empty_metrics(version, horizon, group_by, notes,
                             n=len(usable))
        out["payload"]["source"] = "MISSING: argus_quant.metrics unavailable"
        return out

    cal = QM.calibration_table(y, p, n_bins=10)
    dim = group_by if group_by in ("regime", "sector") else None
    by_group = []
    if dim:
        buckets: Dict[str, List[int]] = {}
        for i, r in enumerate(usable):
            buckets.setdefault(str(r.get("regime") or "UNKNOWN"),
                               []).append(i)
        for name, idx in buckets.items():
            yy = [y[i] for i in idx]
            pp = [p[i] for i in idx]
            by_group.append({
                "regime": name, "n": len(idx),
                "brier": _round(QM.brier_score(yy, pp)),
                "roc_auc": _round(QM.roc_auc(yy, pp)),
                "calibration_error": _round(
                    QM.calibration_table(yy, pp, n_bins=10)[
                        "calibration_error"]),
            })
    payload = {
        "version": version, "horizon": horizon, "group_by": group_by,
        "n": len(usable),
        "brier": _round(QM.brier_score(y, p)),
        "roc_auc": _round(QM.roc_auc(y, p)),
        "calibration_error": _round(cal["calibration_error"]),
        "by_group": by_group,
        "degradation_flags": degradation_flags(version, horizon),
        "data_status": _worst_status(
            [r.get("data_status_overall") for r in usable]),
        "source": src,
        "notes": notes,
        "n_excluded": excluded,
        "computed_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
    }
    as_of = None
    return {"payload": payload, "status": payload["data_status"],
            "as_of": as_of}


def degradation_flags(version: str, horizon: int) -> List[Dict[str, Any]]:
    """Real WATCH/WARN/CRITICAL events from ``degradation_flags`` (else [])."""
    rows = db.fetchall(
        "SELECT detected_at, model_version, horizon, metric, baseline, "
        "current, window_days, severity, acknowledged "
        "FROM degradation_flags WHERE model_version = ? AND horizon = ? "
        "ORDER BY detected_at DESC LIMIT 200",
        (version, horizon))
    out = []
    for r in rows:
        out.append({
            "detected_at": str(r.get("detected_at")),
            "model_version": r.get("model_version"),
            "horizon": r.get("horizon"),
            "metric": r.get("metric"),
            "baseline": _num(r.get("baseline")),
            "current": _num(r.get("current")),
            "window_days": r.get("window_days"),
            "severity": r.get("severity"),
            "acknowledged": bool(r.get("acknowledged")),
        })
    return out


def model_metrics(version: str, horizon: int,
                  group_by: str = "regime") -> Dict[str, Any]:
    """Metrics for (version, horizon) from real sources, else MISSING."""
    if not db.db_configured():
        return _empty_metrics(version, horizon, group_by, [
            "DATABASE_URL/ARGUS_DB_DSN is not set: no Postgres connection, so "
            "neither model_metrics_daily nor prediction_outcomes can be read",
            "the shipped registry carries no realised-outcome metrics for "
            f"{version} h={horizon} (it holds OOF calibration maps only)",
            "no performance claim is made: brier/roc_auc/calibration_error "
            "are null",
        ])
    hit = _from_model_metrics_daily(version, horizon, group_by)
    if hit is not None:
        return hit
    hit = _from_prediction_outcomes(version, horizon, group_by)
    if hit is not None:
        return hit
    return _empty_metrics(version, horizon, group_by, [
        "Postgres reachable but model_metrics_daily returned no row for "
        f"model_version={version} horizon={horizon}",
        "prediction_outcomes returned no matured outcome for that "
        "model_version/horizon",
        "no performance claim is made: brier/roc_auc/calibration_error "
        "are null",
    ])


# ---------------------------------------------------------------------------
# /models/{version}/calibration
# ---------------------------------------------------------------------------

def _buckets_from_calibration_table(table: Dict[str, Any],
                                    source: str) -> List[Dict[str, Any]]:
    """argus_quant.calibration_table -> web CalibrationBucket shape."""
    out = []
    for b in table.get("buckets") or []:
        lo = _num(b.get("bin_low"))
        hi = _num(b.get("bin_high"))
        out.append({
            "bin": [lo, hi],
            "predicted": _num(b.get("predicted_mean")),
            "observed": _num(b.get("observed_mean")),
            "n": int(b.get("n") or 0),
            "n_status": "PRESENT",
            "mean_predicted": _num(b.get("predicted_mean")),
            "observed_rate": _num(b.get("observed_mean")),
            "insufficient": bool(b.get("insufficient")),
            "source": source,
        })
    return out


def _calibration_from_outcomes(version: str,
                               horizon: int) -> Optional[Dict[str, Any]]:
    """Empirical reliability table from realised outcomes (real bucket n)."""
    rows = db.fetchall(
        "SELECT p.p_positive, p.regime, p.data_status_overall, o.actual_return "
        "FROM prediction_outcomes o "
        "JOIN predictions p ON p.id = o.prediction_id "
        "WHERE p.model_version = ? AND p.horizon = ? "
        "AND o.actual_return IS NOT NULL LIMIT 20000",
        (version, horizon))
    usable = [r for r in rows
              if _num(r.get("p_positive")) is not None
              and _num(r.get("actual_return")) is not None]
    if len(usable) < MIN_SAMPLES:
        return None
    try:
        from argus_quant import metrics as QM
    except Exception:  # noqa: BLE001
        return None
    y = [1.0 if _num(r["actual_return"]) > 0 else 0.0 for r in usable]
    p = [_num(r["p_positive"]) for r in usable]
    table = QM.calibration_table(y, p, n_bins=10)
    src = "postgres:prediction_outcomes JOIN predictions"
    return {
        "buckets": _buckets_from_calibration_table(table, src),
        "method": "EMPIRICAL_RELIABILITY",
        "min_samples": MIN_SAMPLES,
        "status": _worst_status([r.get("data_status_overall")
                                 for r in usable]),
        "source": src,
        "n_oof": len(usable),
        "brier_before": None,
        "brier_after": None,
        "fitted_at": None,
        "regimes": [],
        "notes": [
            "realised outcomes recomputed with argus_quant.metrics."
            "calibration_table (equal-width bins, sub-20 bins merged)",
            f"insufficient_bins={table.get('insufficient_bins')}",
        ],
    }


def _calibration_from_maps_doc(doc: Dict[str, Any], path: str,
                               version: str, horizon: int
                               ) -> Optional[Dict[str, Any]]:
    maps = doc.get("maps") or {}
    entry = maps.get("global") or {}
    xs, ys = entry.get("x") or [], entry.get("y") or []
    if not xs or not ys:
        return None
    method = str(doc.get("method") or entry.get("method") or "")
    method = "ISOTONIC" if "isotonic" in method.lower() else (
        method.upper() or "ISOTONIC")
    source = _rel(path)
    return {
        "buckets": _knots_to_buckets(xs, ys, source),
        "method": method,
        "min_samples": int(doc.get("min_samples_per_bucket")
                           or doc.get("min_samples") or MIN_SAMPLES),
        "status": "UNCONFIRMED",
        "source": source,
        "n_oof": int(entry.get("n") or doc.get("n_oof") or 0),
        "brier_before": _num(entry.get("brier_before")
                             if entry.get("brier_before") is not None
                             else doc.get("brier_before")),
        "brier_after": _num(entry.get("brier_after")
                            if entry.get("brier_after") is not None
                            else doc.get("brier_after")),
        "fitted_at": doc.get("fitted_at"),
        "regimes": _regime_summary(maps, source),
        "notes": [
            f"shipped isotonic map read verbatim from {source} "
            f"(fit on out-of-fold predictions only)",
            "curve points are the fitted map knots: predicted = block mean "
            "predicted probability, observed = realised frequency in that "
            "block",
            "the file stores one sample count per regime slice, not per knot: "
            "per-point n is reported as null with n_status=MISSING instead of "
            "being invented",
        ],
    }


def _calibration_from_maps_db(version: str,
                              horizon: int) -> Optional[Dict[str, Any]]:
    row = db.fetchone(
        "SELECT method, buckets, fitted_at, min_samples FROM calibration_maps "
        "WHERE model_version = ? AND horizon = ? "
        "ORDER BY fitted_at DESC LIMIT 1",
        (version, horizon))
    if not row:
        return None
    buckets = row.get("buckets")
    if isinstance(buckets, str):
        try:
            buckets = json.loads(buckets)
        except Exception:  # noqa: BLE001
            return None
    if not isinstance(buckets, dict):
        return None
    doc = {
        "method": row.get("method"),
        "maps": buckets,
        "min_samples_per_bucket": row.get("min_samples"),
        "fitted_at": str(row.get("fitted_at")),
    }
    out = _calibration_from_maps_doc(doc, f"postgres:calibration_maps", version,
                                     horizon)
    return out


def calibration(version: str, horizon: int) -> Dict[str, Any]:
    """Reliability curve for (version, horizon) from real sources, else MISSING."""
    sources: List[str] = []

    if db.db_configured():
        hit = _calibration_from_outcomes(version, horizon)
        if hit is not None:
            return hit
        sources.append("prediction_outcomes: no matured outcome "
                       f"(or fewer than {MIN_SAMPLES}) for {version} h={horizon}")
        hit = _calibration_from_maps_db(version, horizon)
        if hit is not None:
            return hit
        sources.append("calibration_maps: no fitted map row for "
                       f"{version} h={horizon}")
    else:
        sources.append("DATABASE_URL/ARGUS_DB_DSN is not set")

    path = _calibration_doc_path(version, horizon)
    if path:
        try:
            doc = _load_json(path)
        except Exception as exc:  # noqa: BLE001
            doc = None
            sources.append(f"{_rel(path)} unreadable ({type(exc).__name__})")
        if doc:
            hit = _calibration_from_maps_doc(doc, path, version, horizon)
            if hit is not None:
                hit["notes"] += ["registry sources consulted: "
                                 + "; ".join(sources)]
                return hit
    else:
        sources.append(f"no shipped calibration map for {version} h={horizon} "
                       f"(looked for "
                       f"registry/calibration/calibration_{version}_h{horizon}"
                       f".json"
                       + (", registry/calibration_" + version + ".json"
                          if horizon == 20 else "") + ")")

    return {
        "buckets": [],
        "method": "MISSING",
        "min_samples": MIN_SAMPLES,
        "status": "MISSING",
        "source": "MISSING: " + "; ".join(sources),
        "n_oof": None,
        "brier_before": None,
        "brier_after": None,
        "fitted_at": None,
        "regimes": [],
        "notes": sources + [
            "no reliability curve is shown rather than a synthesized one",
        ],
    }


# ---------------------------------------------------------------------------
# /models row summary
# ---------------------------------------------------------------------------

def model_summary(version: str) -> Dict[str, Any]:
    """Measured Brier/ROC-AUC/cal-error/n for a version row (else nulls)."""
    if db.db_configured():
        hit = _from_model_metrics_daily(version, 20, "none")
        if hit is not None:
            p = hit["payload"]
            return {"brier": p["brier"], "roc_auc": p["roc_auc"],
                    "cal_error": p["calibration_error"], "n": p["n"],
                    "status": p["data_status"], "source": p["source"],
                    "notes": p["notes"]}
    oof = registry_oof(version)
    if oof["brier"] is not None or oof["roc_auc"] is not None:
        return {"brier": oof["brier"], "roc_auc": oof["roc_auc"],
                "cal_error": None, "n": oof["n"],
                "status": "UNCONFIRMED", "source": oof["source"],
                "notes": ["registry out-of-fold metrics (real, fit on OOF "
                          "predictions); calibration error is not published "
                          "per version -> null"]}
    return {"brier": None, "roc_auc": None, "cal_error": None, "n": 0,
            "status": "MISSING", "source": oof["source"],
            "notes": ["no measured metrics source for this version: values "
                      "are null and data_status is MISSING, not synthesized"]}


def weights_are_priors(version: str) -> Optional[bool]:
    """True when no regime weight block in the registry is ``learned``."""
    doc = read_registry(version) or {}
    regimes = doc.get("regimes")
    if not isinstance(regimes, dict) or not regimes:
        return None
    learned = [bool((r or {}).get("learned")) for r in regimes.values()
               if isinstance(r, dict)]
    return None if not learned else not any(learned)


# ---------------------------------------------------------------------------
# market data helpers (real prices; nothing synthesized)
# ---------------------------------------------------------------------------

def daily_bars(ticker: str):
    """Real daily OHLCV via app.prices (cache/baked fallback). May raise
    PriceFetchError — callers translate that into an explicit error/MISSING."""
    from app import prices as PX
    return PX.fetch_yahoo_daily(ticker)


def freshness(as_of_date) -> bool:
    """Snapshot freshness (documented rule, derived from the data itself):
    the last bar is within 10 calendar days of today. Used to choose between
    data_status LIKELY (fresh) and UNCONFIRMED (stale snapshot)."""
    try:
        today = datetime.now(timezone.utc).date()
        return (today - as_of_date) <= timedelta(days=10)
    except Exception:  # noqa: BLE001
        return False


def price_summary(ticker: str) -> Optional[Dict[str, Any]]:
    """Last close / previous close / change ratio from real bars."""
    df = daily_bars(ticker)
    closes = df["close"].dropna()
    if len(closes) < 2:
        return None
    last, prev = _num(closes.iloc[-1]), _num(closes.iloc[-2])
    if last is None or not prev:
        return None
    as_of = closes.index[-1]
    as_of_date = as_of.date() if hasattr(as_of, "date") else as_of
    return {
        "price": last,
        "prev_close": prev,
        "change_pct": round((last - prev) / prev, 6),  # RATIO (web x100)
        "as_of": str(as_of_date),
        "fresh": freshness(as_of_date),
        "bars": int(len(df)),
        "source": "app.prices.fetch_yahoo_daily (Yahoo daily; baked snapshot "
                  "fallback)",
    }


def _ema(vals, span: int):
    import numpy as np
    v = np.asarray(vals, dtype=float)
    if v.size == 0:
        return None
    alpha = 2.0 / (span + 1.0)
    out = v[0]
    for x in v[1:]:
        out = alpha * x + (1 - alpha) * out
    return float(out)


def _ema_series(vals, span: int):
    import numpy as np
    v = np.asarray(vals, dtype=float)
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(v)
    out[0] = v[0]
    for i in range(1, len(v)):
        out[i] = alpha * v[i] + (1 - alpha) * out[i - 1]
    return out


def technicals(ticker: str) -> Optional[Dict[str, Any]]:
    """Indicators computed from REAL daily bars (formulas documented inline)."""
    import numpy as np

    df = daily_bars(ticker)
    close = df["close"].astype(float).dropna()
    if len(close) < 30:
        return None
    high = df["high"].astype(float).reindex(close.index)
    low = df["low"].astype(float).reindex(close.index)
    c = close.to_numpy()

    # RSI(14) - Wilder smoothing.
    delta = np.diff(c)
    gain = np.clip(delta, 0, None)
    loss = np.clip(-delta, 0, None)
    ag = gain[:14].mean() if len(gain) >= 14 else gain.mean()
    al = loss[:14].mean() if len(loss) >= 14 else loss.mean()
    for i in range(14, len(delta)):
        ag = (ag * 13 + gain[i]) / 14
        al = (al * 13 + loss[i]) / 14
    rsi = 100.0 - 100.0 / (1 + ag / al) if al > 0 else 100.0

    # MACD(12,26) and signal EMA(9) of MACD.
    macd_series = _ema_series(c, 12) - _ema_series(c, 26)
    macd = float(macd_series[-1])
    macd_signal = float(_ema_series(macd_series, 9)[-1])

    sma_50 = float(c[-50:].mean()) if len(c) >= 50 else None
    sma_200 = float(c[-200:].mean()) if len(c) >= 200 else None
    std20 = float(c[-20:].std(ddof=0))
    boll_upper = float(c[-20:].mean() + 2 * std20)
    boll_lower = float(c[-20:].mean() - 2 * std20)

    # ATR(14) - Wilder smoothing of the true range.
    h = high.to_numpy(dtype=float)
    lo = low.to_numpy(dtype=float)
    prev_close = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev_close),
                                       np.abs(lo - prev_close)))
    atr = float(tr[:14].mean()) if len(tr) >= 14 else float(tr.mean())
    for i in range(14, len(tr)):
        atr = (atr * 13 + tr[i]) / 14

    as_of = close.index[-1]
    return {
        "as_of": str(as_of.date() if hasattr(as_of, "date") else as_of),
        "rsi_14": round(float(rsi), 4),
        "macd": round(float(macd), 6),
        "macd_signal": round(float(macd_signal), 6),
        "sma_50": round(float(sma_50), 4) if sma_50 is not None else None,
        "sma_200": round(float(sma_200), 4) if sma_200 is not None else None,
        "bollinger_upper": round(float(boll_upper), 4),
        "bollinger_lower": round(float(boll_lower), 4),
        "atr_14": round(float(atr), 4),
        "source": "computed from app.prices.fetch_yahoo_daily bars",
        "fresh": freshness(as_of.date() if hasattr(as_of, "date") else as_of),
    }


def fundamentals(ticker: str, price: float) -> Optional[Dict[str, Any]]:
    """Real point-in-time fundamentals via the EDGAR connector (or None).

    No value is invented: fields the SEC facts cannot support stay None, and
    when the connector cannot reach EDGAR the router reports MISSING.
    """
    try:
        from argus_quant.data_connectors import edgar
    except Exception:  # noqa: BLE001
        return None
    as_of = datetime.now(timezone.utc).date().isoformat()
    try:
        raw = edgar.fundamentals_for_ticker(ticker, as_of, price)
    except Exception as exc:  # noqa: BLE001 - network/parse failure -> MISSING
        log.warning("EDGAR fundamentals failed for %s: %s", ticker,
                    type(exc).__name__)
        return None
    if not raw:
        return None
    return {
        "fiscal_period": None,  # EDGAR facts are TTM/point-in-time, not filed
                                # as a fiscal-period label here
        "reported_at": raw.get("reported_at"),
        "revenue": _num(raw.get("ttm_revenue")),
        "eps": None,           # connector does not publish EPS -> null
        "ebitda": None,        # connector publishes EV/EBITDA, not EBITDA
        "gross_margin": _num(raw.get("gross_margin")),
        "net_margin": _num(raw.get("net_margin")),
        "roe": _num(raw.get("roe")),
        "pe": _num(raw.get("pe")),
        "forward_pe": None,    # no forward estimates in EDGAR -> null
        "source": "argus_quant.data_connectors.edgar."
                  "fundamentals_for_ticker (SEC company facts)",
    }


# ---------------------------------------------------------------------------
# price-derived signal strengths (real math on real bars)
# ---------------------------------------------------------------------------

def _sigmoid(z: float) -> float:
    import math as _m
    return 1.0 / (1.0 + _m.exp(-max(-60.0, min(60.0, z))))


def signal_strengths(ticker: str, spy_close=None) -> Optional[Dict[str, Any]]:
    """Real, documented indicator-derived signal strengths in [0, 1].

    Replacements for the previous sha256("argus-stub:signal:...") hash, which
    produced a number that looked like a strength but measured nothing:

      * breakout       Donchian position: (close - low20) / (high20 - low20)
      * momentum       sigmoid(63d return / (daily sigma * sqrt(63)))
      * mean_reversion sigmoid(-z20), z20 = (close - sma20) / std20
      * reversal       sigmoid(-(RSI14 - 50) / 15)  (oversold -> high)
      * rotation       sigmoid((r63_t - r63_spy) / (sigma_t * sqrt(63)))
    """
    import numpy as np

    df = daily_bars(ticker)
    c = df["close"].astype(float).dropna()
    if len(c) < 70:
        return None
    arr = c.to_numpy()
    last = float(arr[-1])
    high20, low20 = float(arr[-20:].max()), float(arr[-20:].min())
    rng = high20 - low20
    breakout = (last - low20) / rng if rng > 0 else 0.5

    rets = np.diff(arr) / arr[:-1]
    sigma = float(rets[-252:].std(ddof=0)) or 1e-9
    r63 = last / float(arr[-64]) - 1.0
    momentum = _sigmoid(r63 / (sigma * np.sqrt(63)))
    z20 = (last - float(arr[-20:].mean())) / (float(arr[-20:].std(ddof=0))
                                              or 1e-9)
    mean_rev = _sigmoid(-z20)

    # RSI(14), Wilder smoothing (same formula as observatory.technicals).
    delta = np.diff(arr)
    gain = np.clip(delta, 0, None)
    loss = np.clip(-delta, 0, None)
    ag, al = float(gain[:14].mean()), float(loss[:14].mean())
    for i in range(14, len(delta)):
        ag = (ag * 13 + gain[i]) / 14
        al = (al * 13 + loss[i]) / 14
    rsi = 100.0 - 100.0 / (1 + ag / al) if al > 0 else 100.0
    reversal = _sigmoid(-(rsi - 50.0) / 15.0)

    rotation = None
    if spy_close is not None and len(spy_close) >= 64:
        spy = np.asarray(spy_close, dtype=float)
        r63_spy = float(spy[-1]) / float(spy[-64]) - 1.0
        rotation = _sigmoid((r63 - r63_spy) / (sigma * np.sqrt(63)))

    as_of = c.index[-1]
    as_of_date = as_of.date() if hasattr(as_of, "date") else as_of
    return {
        "strengths": {
            "breakout": round(float(min(max(breakout, 0.0), 1.0)), 4),
            "momentum": round(float(momentum), 4),
            "mean_reversion": round(float(mean_rev), 4),
            "reversal": round(float(reversal), 4),
            "rotation": (round(float(rotation), 4)
                         if rotation is not None else None),
        },
        "as_of": str(as_of_date),
        "fresh": freshness(as_of_date),
        "inputs": {
            "last_close": last, "donchian_low20": low20,
            "donchian_high20": high20, "sigma_daily": round(sigma, 6),
            "rsi_14": round(rsi, 4), "return_63d": round(r63, 6),
            "z20": round(z20, 4),
        },
        "source": "computed from app.prices.fetch_yahoo_daily bars",
    }


def market_regime() -> Optional[Dict[str, Any]]:
    """Real regime classification from real SPY/VIX bars (argus_quant.regime)."""
    from argus_quant import regime as RG

    px = {}
    for name in ("SPY", "^VIX"):
        px[name] = daily_bars(name)
    spy, vix = px["SPY"]["close"].dropna(), px["^VIX"]["close"].dropna()
    if len(spy) < 200 or len(vix) < 21:
        return None
    as_of = spy.index[-1]
    try:
        trend = float(spy.iloc[-1] / spy.rolling(200).mean().iloc[-1] - 1.0)
    except Exception:  # noqa: BLE001
        return None
    res = RG.classify_regime(
        as_of, trend=trend, vix=vix,
        vix_chg20=float(vix.iloc[-1] / vix.iloc[-21] - 1.0))
    if not res:
        return None
    as_of_date = as_of.date() if hasattr(as_of, "date") else as_of
    return {
        "regime": res.get("regime", "Neutral"),
        "confidence": round(float(res.get("confidence", 0) or 0) / 100.0, 4),
        "as_of": str(as_of_date),
        "drivers": res.get("drivers", []),
        "data_status": res.get("data_status", "MISSING"),
        "inputs": res.get("inputs", {}),
        "source": "argus_quant.regime.classify_regime on real SPY/^VIX bars",
        "fresh": freshness(as_of_date),
    }


def regime_history_csv(days: int = 365) -> List[Dict[str, Any]]:
    """Real regime history from data/regime_history.csv ([] when absent)."""
    import csv
    path = os.path.join(REPO, "data", "regime_history.csv")
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for rec in csv.DictReader(fh):
                rows.append({
                    "time": rec["time"][:10],
                    "regime": rec["regime"],
                    "confidence": float(rec["confidence"]),
                    "data_status": rec.get("data_status", "UNCONFIRMED"),
                })
    except (OSError, KeyError, ValueError):
        return []
    return rows[-days:]
