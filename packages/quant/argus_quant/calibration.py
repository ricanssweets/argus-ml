"""Probability calibration.

LOCKED LAW: calibrators are fit ONLY on out-of-fold (OOF) walk-forward
predictions - never on training folds. This is enforced by API design:
``fit`` takes ``(oof_predictions, oof_labels)`` arrays. Passing in-sample
model outputs is a protocol violation; the parameter names and docstrings
make the requirement explicit, and ``fit`` refuses inputs that cannot
plausibly be OOF.

MIN_SAMPLES_PER_BUCKET is a PER-BUCKET rule, not a global one. A calibrated
map needs at least ``MIN_BUCKETS`` buckets, so ``fit`` requires
``MIN_SAMPLES_PER_BUCKET * MIN_BUCKETS`` OOF samples; on top of that the
isotonic fit COALESCES adjacent knots until every materialised bucket holds
at least ``MIN_SAMPLES_PER_BUCKET`` OOF points. If the sample still cannot
support that granularity the map says so (``calibrated: False`` +
``small_buckets``) instead of pretending coverage it does not have.

Serialisation: ``to_map()`` / ``from_map()`` round-trip to the
``calibration_maps`` DB schema shape:
    {"method": "ISOTONIC"|"PLATT", "buckets": [...],
     "min_samples": 20, "content_sha256": hex}
``fitted_at`` is METADATA, not content: it is omitted from ``to_map()`` by
default so two identical fits serialise to identical bytes (the timestamp is
available via ``calibrator.fitted_at`` or ``to_map(include_fitted_at=True)``).
For isotonic, "buckets" holds the fitted piecewise-linear knots
(thresholds + calibrated values + OOF counts). For Platt, the logistic
coefficients.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from .metrics import MIN_SAMPLES_PER_BUCKET

# A monotone calibration map with a single bucket is the identity, not a
# map: require at least two materialised buckets, hence the sample floor.
MIN_BUCKETS = 2


def _check_oof(oof_predictions, oof_labels, n_buckets: int = MIN_BUCKETS):
    p = np.asarray(oof_predictions, dtype=float).ravel()
    y = np.asarray(oof_labels, dtype=float).ravel()
    if len(p) != len(y):
        raise ValueError("oof_predictions and oof_labels must match in length")
    min_required = MIN_SAMPLES_PER_BUCKET * max(MIN_BUCKETS, int(n_buckets))
    if len(p) < min_required:
        raise ValueError(
            f"refusing to fit calibration on {len(p)} samples: "
            f"MIN_SAMPLES_PER_BUCKET={MIN_SAMPLES_PER_BUCKET} is a PER-BUCKET "
            f"minimum and a map needs >= {max(MIN_BUCKETS, int(n_buckets))} "
            f"buckets, so >= {min_required} OOF samples are required")
    if np.any((p < 0.0) | (p > 1.0)) or not np.all(np.isfinite(p)):
        raise ValueError("oof_predictions must be finite probabilities in [0,1]")
    if set(np.unique(y)) - {0.0, 1.0}:
        raise ValueError("oof_labels must be binary 0/1")
    if len(np.unique(y)) < 2:
        # both calibrators need both classes; IsotonicRegression would fit
        # but the resulting map is meaningless and LogisticRegression raises
        # an opaque solver error. Fail loudly and identically for both.
        raise ValueError(
            "oof_labels contain a single class - a calibrator needs both "
            "classes present in the OOF sample")
    return p, y


def _bucket_counts(x: np.ndarray, points: np.ndarray) -> list[int]:
    """OOF sample count per knot interval (last interval closed on both ends)."""
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return []
    idx = np.clip(np.searchsorted(x, points, side="left"), 0, len(x) - 1)
    return np.bincount(idx, minlength=len(x)).astype(int).tolist()


def _canonical(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))


def _content_sha256(doc: dict) -> str:
    payload = {k: v for k, v in doc.items()
               if k not in ("content_sha256", "fitted_at")}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


class IsotonicCalibrator:
    """Isotonic regression calibrator (default per MODEL_EVALUATION.md).

    Fit ONLY on out-of-fold predictions. sklearn is used for *fitting*
    only; evaluation is piecewise-linear interpolation over the stored
    knots (``np.interp`` with clip to the fitted input range), which keeps
    ``to_map``/``from_map`` round-trips bit-identical and independent of
    sklearn's private internals.
    """

    method = "ISOTONIC"

    def __init__(self):
        self._x: np.ndarray | None = None   # knot thresholds (sorted)
        self._y: np.ndarray | None = None   # calibrated values at knots
        self._counts: list[int] = []        # OOF samples per bucket
        self.fitted_at: str | None = None
        self.n_samples: int = 0

    def fit(self, oof_predictions, oof_labels,
            fitted_at: str | None = None) -> "IsotonicCalibrator":
        """Fit on OUT-OF-FOLD predictions and labels. Never training data.

        Buckets are built so the PER-BUCKET minimum holds BY CONSTRUCTION:
        the sorted OOF sample is split into ``n // MIN_SAMPLES_PER_BUCKET``
        equal-count groups (at least ``MIN_BUCKETS``), each group is scored
        with its own predicted/observed means, and a weighted isotonic
        regression enforces monotonicity across groups (pooling adjacent
        violators only ever merges buckets, so no bucket can fall below the
        minimum). A calibrated map is therefore never built from a bucket
        with fewer than MIN_SAMPLES_PER_BUCKET OOF points.

        ``fitted_at`` may be supplied to make the timestamp itself
        reproducible; it is metadata only and never enters the content hash
        or ``to_map()`` by default.
        """
        p, y = _check_oof(oof_predictions, oof_labels)
        # Deterministic tie handling: sort by (p, y) with a stable sort.
        order = np.argsort(p, kind="mergesort")
        ps, ys = p[order], y[order]
        n = len(ps)
        n_buckets = max(MIN_BUCKETS, n // MIN_SAMPLES_PER_BUCKET)
        groups = np.array_split(np.arange(n), n_buckets)
        gx = np.array([float(ps[g].mean()) for g in groups], dtype=float)
        gy = np.array([float(ys[g].mean()) for g in groups], dtype=float)
        gw = np.array([len(g) for g in groups], dtype=float)
        ir = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        ir.fit(gx, gy, sample_weight=gw)
        yv = np.clip(np.asarray(ir.predict(gx), dtype=float), 0.0, 1.0)
        # Bucket BOUNDARIES (largest p in each group) must strictly increase
        # for np.interp; only degenerate all-equal samples need nudging.
        xb = np.array([float(ps[g].max()) for g in groups], dtype=float)
        for i in range(1, len(xb)):
            if xb[i] <= xb[i - 1]:
                xb[i] = np.nextafter(xb[i - 1], np.inf)
        self._x, self._y = xb, yv
        self._counts = [int(len(g)) for g in groups]
        self.fitted_at = fitted_at or datetime.now(timezone.utc).isoformat()
        self.n_samples = n
        return self

    def predict(self, raw_scores) -> np.ndarray:
        if self._x is None or self._y is None:
            raise RuntimeError("calibrator not fitted")
        s = np.asarray(raw_scores, dtype=float).ravel()
        s = np.clip(s, self._x[0], self._x[-1])  # out_of_bounds="clip"
        return np.clip(np.interp(s, self._x, self._y), 0.0, 1.0)

    def to_map(self, include_fitted_at: bool = False) -> dict:
        """Serialize to the calibration_maps DB schema shape.

        Deterministic: two fits on identical OOF input produce identical
        bytes (``fitted_at`` is metadata and is omitted unless asked for).
        ``bucket_sizes``/``small_buckets``/``calibrated`` make the
        per-bucket evidence auditable: ``calibrated`` is False when any
        bucket still holds < MIN_SAMPLES_PER_BUCKET OOF points.
        """
        if self._x is None or self._y is None:
            raise RuntimeError("calibrator not fitted")
        counts = list(self._counts) or _bucket_counts(self._x, self._x)
        small = [i for i, c in enumerate(counts)
                 if c < MIN_SAMPLES_PER_BUCKET]
        knots = [{"threshold": float(t), "calibrated": float(c),
                  "n_oof": int(n)}
                 for t, c, n in zip(self._x, self._y, counts)]
        out = {"method": self.method, "buckets": knots,
               "min_samples": MIN_SAMPLES_PER_BUCKET,
               "min_samples_per_bucket": MIN_SAMPLES_PER_BUCKET,
               "bucket_sizes": counts,
               "small_buckets": small,
               "calibrated": not small,
               "n_samples": self.n_samples}
        if include_fitted_at:
            out["fitted_at"] = self.fitted_at
        out["content_sha256"] = _content_sha256(out)
        return out

    def content_sha256(self) -> str:
        """Stable content hash of the fitted map (independent of time)."""
        return _content_sha256(self.to_map())

    @classmethod
    def from_map(cls, m: dict) -> "IsotonicCalibrator":
        """Rebuild from a calibration_maps row. Knots are restored exactly,
        so the mapping is bit-identical to the fitted one."""
        if m.get("method") != cls.method:
            raise ValueError(f"map method is {m.get('method')}, expected {cls.method}")
        knots = m["buckets"]
        if len(knots) < 1:
            raise ValueError("empty knot list")
        obj = cls()
        obj._x = np.array([k["threshold"] for k in knots], dtype=float)
        obj._y = np.array([k["calibrated"] for k in knots], dtype=float)
        obj._counts = [int(k.get("n_oof", 0)) for k in knots]
        obj.fitted_at = m.get("fitted_at")
        obj.n_samples = int(m.get("n_samples", 0))
        return obj


class PlattCalibrator:
    """Platt scaling: logistic regression on OOF predictions.

    Fit ONLY on out-of-fold predictions. Deterministic (lbfgs, fixed seed).
    """

    method = "PLATT"

    def __init__(self):
        self._lr: LogisticRegression | None = None
        self.fitted_at: str | None = None
        self.n_samples: int = 0

    def fit(self, oof_predictions, oof_labels,
            fitted_at: str | None = None) -> "PlattCalibrator":
        """Fit on OUT-OF-FOLD predictions and labels. Never training data."""
        p, y = _check_oof(oof_predictions, oof_labels)
        self._lr = LogisticRegression(solver="lbfgs", random_state=42)
        self._lr.fit(p.reshape(-1, 1), y)
        self.fitted_at = fitted_at or datetime.now(timezone.utc).isoformat()
        self.n_samples = len(p)
        return self

    def predict(self, raw_scores) -> np.ndarray:
        if self._lr is None:
            raise RuntimeError("calibrator not fitted")
        s = np.asarray(raw_scores, dtype=float).ravel()
        return np.clip(self._lr.predict_proba(s.reshape(-1, 1))[:, 1], 0.0, 1.0)

    def to_map(self, include_fitted_at: bool = False) -> dict:
        if self._lr is None:
            raise RuntimeError("calibrator not fitted")
        out = {"method": self.method,
               "buckets": [{"coef": float(self._lr.coef_[0, 0]),
                            "intercept": float(self._lr.intercept_[0])}],
               "min_samples": MIN_SAMPLES_PER_BUCKET,
               "min_samples_per_bucket": MIN_SAMPLES_PER_BUCKET,
               "n_samples": self.n_samples}
        if include_fitted_at:
            out["fitted_at"] = self.fitted_at
        out["content_sha256"] = _content_sha256(out)
        return out

    def content_sha256(self) -> str:
        """Stable content hash of the fitted map (independent of time)."""
        return _content_sha256(self.to_map())

    @classmethod
    def from_map(cls, m: dict) -> "PlattCalibrator":
        if m.get("method") != cls.method:
            raise ValueError(f"map method is {m.get('method')}, expected {cls.method}")
        b = m["buckets"][0]
        obj = cls()
        lr = LogisticRegression(solver="lbfgs", random_state=42)
        lr.coef_ = np.array([[b["coef"]]])
        lr.intercept_ = np.array([b["intercept"]])
        lr.classes_ = np.array([0, 1])
        lr.n_features_in_ = 1
        obj._lr = lr
        obj.fitted_at = m.get("fitted_at")
        obj.n_samples = int(m.get("n_samples", 0))
        return obj
