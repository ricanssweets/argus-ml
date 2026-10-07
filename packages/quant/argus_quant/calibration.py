"""Probability calibration.

LOCKED LAW: calibrators are fit ONLY on out-of-fold (OOF) walk-forward
predictions - never on training folds. This is enforced by API design:
``fit`` takes ``(oof_predictions, oof_labels)`` arrays. Passing in-sample
model outputs is a protocol violation; the parameter names and docstrings
make the requirement explicit, and ``fit`` refuses inputs that cannot
plausibly be OOF (fewer than MIN_SAMPLES_PER_BUCKET samples).

Serialisation: ``to_map()`` / ``from_map()`` round-trip to the
``calibration_maps`` DB schema shape:
    {"method": "ISOTONIC"|"PLATT", "buckets": [...],
     "fitted_at": iso, "min_samples": 20}
For isotonic, "buckets" holds the fitted piecewise-linear knots
(thresholds + calibrated values). For Platt, the logistic coefficients.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from .metrics import MIN_SAMPLES_PER_BUCKET


def _check_oof(oof_predictions, oof_labels):
    p = np.asarray(oof_predictions, dtype=float).ravel()
    y = np.asarray(oof_labels, dtype=float).ravel()
    if len(p) != len(y):
        raise ValueError("oof_predictions and oof_labels must match in length")
    if len(p) < MIN_SAMPLES_PER_BUCKET:
        raise ValueError(
            f"refusing to fit calibration on {len(p)} samples: "
            f"minimum is {MIN_SAMPLES_PER_BUCKET} (MIN_SAMPLES_PER_BUCKET)")
    if np.any((p < 0.0) | (p > 1.0)) or not np.all(np.isfinite(p)):
        raise ValueError("oof_predictions must be finite probabilities in [0,1]")
    if set(np.unique(y)) - {0.0, 1.0}:
        raise ValueError("oof_labels must be binary 0/1")
    return p, y


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
        self.fitted_at: str | None = None
        self.n_samples: int = 0

    def fit(self, oof_predictions, oof_labels) -> "IsotonicCalibrator":
        """Fit on OUT-OF-FOLD predictions and labels. Never training data."""
        p, y = _check_oof(oof_predictions, oof_labels)
        # Deterministic tie handling: sort by (p, y) with a stable sort.
        order = np.argsort(p, kind="mergesort")
        ir = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        ir.fit(p[order], y[order])
        self._x = np.asarray(ir.X_thresholds_, dtype=float)
        self._y = np.asarray(ir.y_thresholds_, dtype=float)
        self.fitted_at = datetime.now(timezone.utc).isoformat()
        self.n_samples = len(p)
        return self

    def predict(self, raw_scores) -> np.ndarray:
        if self._x is None or self._y is None:
            raise RuntimeError("calibrator not fitted")
        s = np.asarray(raw_scores, dtype=float).ravel()
        s = np.clip(s, self._x[0], self._x[-1])  # out_of_bounds="clip"
        return np.clip(np.interp(s, self._x, self._y), 0.0, 1.0)

    def to_map(self) -> dict:
        """Serialize to the calibration_maps DB schema shape."""
        if self._x is None or self._y is None:
            raise RuntimeError("calibrator not fitted")
        knots = [{"threshold": float(t), "calibrated": float(c)}
                 for t, c in zip(self._x, self._y)]
        return {"method": self.method, "buckets": knots,
                "fitted_at": self.fitted_at, "min_samples": MIN_SAMPLES_PER_BUCKET,
                "n_samples": self.n_samples}

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

    def fit(self, oof_predictions, oof_labels) -> "PlattCalibrator":
        """Fit on OUT-OF-FOLD predictions and labels. Never training data."""
        p, y = _check_oof(oof_predictions, oof_labels)
        self._lr = LogisticRegression(solver="lbfgs", random_state=42)
        self._lr.fit(p.reshape(-1, 1), y)
        self.fitted_at = datetime.now(timezone.utc).isoformat()
        self.n_samples = len(p)
        return self

    def predict(self, raw_scores) -> np.ndarray:
        if self._lr is None:
            raise RuntimeError("calibrator not fitted")
        s = np.asarray(raw_scores, dtype=float).ravel()
        return np.clip(self._lr.predict_proba(s.reshape(-1, 1))[:, 1], 0.0, 1.0)

    def to_map(self) -> dict:
        if self._lr is None:
            raise RuntimeError("calibrator not fitted")
        return {"method": self.method,
                "buckets": [{"coef": float(self._lr.coef_[0, 0]),
                             "intercept": float(self._lr.intercept_[0])}],
                "fitted_at": self.fitted_at, "min_samples": MIN_SAMPLES_PER_BUCKET,
                "n_samples": self.n_samples}

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
