"""ARGUS-EQ-1.0 — versioned sklearn ensemble with stacking meta-learner.

Pipeline position (ARCHITECTURE.md §2–§3, step 8): regime-conditioned
ensemble over base learners, pinned to version ARGUS-EQ-1.0. Weights come
from app/registry/ARGUS-EQ-1.0.json and are ARCHITECTURE.md §5 PRIORS
pending walk-forward learning — they are documented as such, never
presented as learned.

Design:
- Base learners: GradientBoostingClassifier, RandomForestClassifier,
  LogisticRegression, MLPClassifier (+ XGBoost / LightGBM only if
  importable — both optional).
- Stacking: out-of-fold (StratifiedKFold, fixed seed) base predictions feed
  a LogisticRegression meta-learner.
- Regime conditioning: predict_proba(X, regime) blends the stacked output
  with a registry-weighted average of base learners, using the per-horizon /
  per-regime engine priors and a documented learner→engine mapping.
- Calibration: optional isotonic regression fit on out-of-fold predictions
  (MODEL_EVALUATION.md §3 — fit on OOF only, never on training folds).
- Seeds fixed everywhere; n_jobs=1 for determinism.

The walk-forward helper prefers quant validation splitters
(argus_quant.validation, built by the parallel track) and falls back to a
clearly-labeled internal expanding-window splitter only when quant is not
importable.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin, clone
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.neural_network import MLPClassifier

from app.quant_compat import QUANT_AVAILABLE, quant_attr

log = logging.getLogger("argus.ml.ensemble")

VERSION = "ARGUS-EQ-1.0"
DEFAULT_REGISTRY = (
    Path(__file__).parent / "registry" / f"{VERSION}.json"
)

# Scaffold mapping from base learner -> dominant engine family for the
# regime-conditioned prior blend. This is an explicit scaffold mapping, not
# a learned or validated claim; walk-forward learning will replace the need
# for it.
LEARNER_ENGINE_MAP: Dict[str, str] = {
    "gbc": "Technical",
    "rf": "Technical",
    "logreg": "Valuation",
    "mlp": "Momentum",
    "xgb": "Technical",
    "lgbm": "Momentum",
}

# Blend weight between the stacked meta-learner output and the
# registry-prior-weighted learner average. Scaffold constant.
PRIOR_BLEND_ALPHA = 0.30


def _optional_learners(random_state: int) -> Dict[str, object]:
    learners: Dict[str, object] = {}
    try:
        from xgboost import XGBClassifier  # type: ignore[import-not-found]

        learners["xgb"] = XGBClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=random_state,
            n_jobs=1,
            eval_metric="logloss",
        )
        log.info("XGBoost available — included as base learner")
    except ImportError:
        log.info("XGBoost not importable — skipped (optional)")
    try:
        from lightgbm import LGBMClassifier  # type: ignore[import-not-found]

        learners["lgbm"] = LGBMClassifier(
            n_estimators=200,
            max_depth=-1,
            num_leaves=31,
            learning_rate=0.05,
            random_state=random_state,
            n_jobs=1,
            verbose=-1,
        )
        log.info("LightGBM available — included as base learner")
    except ImportError:
        log.info("LightGBM not importable — skipped (optional)")
    return learners


class ArgusEnsemble(BaseEstimator, ClassifierMixin):
    """Versioned stacking ensemble: ARGUS-EQ-1.0."""

    version = VERSION

    def __init__(
        self,
        horizon: int = 20,
        registry_path: Optional[Path] = None,
        random_state: int = 42,
        n_oof_splits: int = 5,
        calibrate: bool = True,
    ) -> None:
        self.horizon = horizon
        self.registry_path = registry_path  # stored unchanged for sklearn clone()
        self.random_state = random_state
        self.n_oof_splits = n_oof_splits
        self.calibrate = calibrate
        self.registry: Dict = {}
        self.learners_: Dict[str, object] = {}
        self.meta_: Optional[LogisticRegression] = None
        self.calibrator_: Optional[IsotonicRegression] = None
        self.feature_names_: Optional[List[str]] = None
        self._load_registry()

    # ------------------------------------------------------------------ setup
    def _load_registry(self) -> None:
        path = Path(self.registry_path) if self.registry_path else DEFAULT_REGISTRY
        with open(path) as f:
            self.registry = json.load(f)
        assert self.registry.get("version") == VERSION, (
            f"registry version mismatch: {self.registry.get('version')}"
        )
        # Validate every (horizon, regime) row sums to 1.
        for h, hrow in self.registry["horizons"].items():
            for r, rrow in hrow["regimes"].items():
                s = sum(rrow["engines"].values())
                assert abs(s - 1.0) < 1e-9, f"weights for h={h} r={r} sum to {s}"
        log.info("loaded ensemble registry %s (%s)", VERSION, path)

    def get_weights(self, horizon: Optional[int] = None, regime: str = "Sideways") -> Dict[str, float]:
        """Regime-conditioned engine weights for (horizon, regime)."""
        h = str(horizon if horizon is not None else self.horizon)
        hrow = self.registry["horizons"].get(h)
        if hrow is None:
            raise KeyError(f"no registry row for horizon {h}")
        rrow = hrow["regimes"].get(regime)
        if rrow is None:
            raise KeyError(f"no registry row for regime {regime!r}")
        return dict(rrow["engines"])

    def weights_are_priors(self, horizon: Optional[int] = None, regime: str = "Sideways") -> bool:
        h = str(horizon if horizon is not None else self.horizon)
        return not self.registry["horizons"][h]["regimes"][regime]["learned"]

    def _build_learners(self) -> Dict[str, object]:
        rs = self.random_state
        learners: Dict[str, object] = {
            "gbc": GradientBoostingClassifier(random_state=rs),
            "rf": RandomForestClassifier(
                n_estimators=300, max_depth=None, min_samples_leaf=5,
                random_state=rs, n_jobs=1,
            ),
            "logreg": LogisticRegression(max_iter=2000, random_state=rs),
            "mlp": MLPClassifier(
                hidden_layer_sizes=(64, 32), max_iter=800,
                early_stopping=True, random_state=rs,
            ),
        }
        learners.update(_optional_learners(rs))
        return learners

    # --------------------------------------------------------------------- fit
    def fit(self, X, y, sample_weight=None):
        X = np.asarray(X, dtype=float)
        y = np.asarray(y).ravel()
        n = X.shape[0]
        if n < 2 * self.n_oof_splits:
            raise ValueError(
                f"need >= {2 * self.n_oof_splits} samples, got {n}"
            )
        learners = self._build_learners()
        self.learners_ = {k: clone(v) for k, v in learners.items()}

        # Out-of-fold predictions for stacking + calibration (OOF only).
        skf = StratifiedKFold(
            n_splits=self.n_oof_splits, shuffle=True, random_state=self.random_state
        )
        names = list(self.learners_.keys())
        oof = np.zeros((n, len(names)))
        for tr, va in skf.split(X, y):
            sw_tr = sample_weight[tr] if sample_weight is not None else None
            for j, name in enumerate(names):
                est = clone(learners[name])
                if sw_tr is not None:
                    try:
                        est.fit(X[tr], y[tr], sample_weight=sw_tr)
                    except TypeError:
                        est.fit(X[tr], y[tr])  # learner ignores sample_weight
                else:
                    est.fit(X[tr], y[tr])
                oof[va, j] = est.predict_proba(X[va])[:, 1]

        # Refit base learners on the full training set.
        for name, est in self.learners_.items():
            if sample_weight is not None:
                try:
                    est.fit(X, y, sample_weight=sample_weight)
                    continue
                except TypeError:
                    pass
            est.fit(X, y)

        # Stacking meta-learner on OOF predictions.
        self.meta_ = LogisticRegression(max_iter=2000, random_state=self.random_state)
        self.meta_.fit(oof, y)

        # Isotonic calibration on OOF meta scores (never on training folds).
        if self.calibrate:
            meta_oof = self.meta_.predict_proba(oof)[:, 1]
            self.calibrator_ = IsotonicRegression(out_of_bounds="clip")
            self.calibrator_.fit(meta_oof, y)
        self.feature_names_ = [f"f{i}" for i in range(X.shape[1])]
        return self

    # --------------------------------------------------------------- predict
    def _learner_probas(self, X) -> Tuple[List[str], np.ndarray]:
        X = np.asarray(X, dtype=float)
        names = list(self.learners_.keys())
        Z = np.column_stack(
            [self.learners_[name].predict_proba(X)[:, 1] for name in names]
        )
        return names, Z

    def _prior_blend_weights(self, regime: str) -> np.ndarray:
        """Map registry engine priors -> per-learner weights (scaffold map)."""
        engine_w = self.get_weights(regime=regime)
        names = list(self.learners_.keys())
        w = np.array(
            [engine_w[LEARNER_ENGINE_MAP[n]] for n in names], dtype=float
        )
        # Renormalize over the learners actually present.
        return w / w.sum()

    def predict_proba(self, X, regime: str = "Sideways") -> np.ndarray:
        if not self.learners_ or self.meta_ is None:
            raise RuntimeError("ensemble is not fitted — call fit() first")
        names, Z = self._learner_probas(X)
        p_meta = self.meta_.predict_proba(Z)[:, 1]
        w = self._prior_blend_weights(regime)
        p_prior = Z @ w
        p = (1.0 - PRIOR_BLEND_ALPHA) * p_meta + PRIOR_BLEND_ALPHA * p_prior
        if self.calibrator_ is not None:
            p = self.calibrator_.predict(p)
        p = np.clip(p, 0.0, 1.0)
        return np.column_stack([1.0 - p, p])

    def predict(self, X, regime: str = "Sideways") -> np.ndarray:
        return (self.predict_proba(X, regime)[:, 1] >= 0.5).astype(int)

    # ------------------------------------------------- walk-forward helper
    def walk_forward_scores(
        self,
        X,
        y,
        n_splits: int = 5,
        min_train: Optional[int] = None,
        metric: str = "brier",
    ) -> Dict[str, object]:
        """Expanding-window walk-forward evaluation.

        Prefers quant validation splitters (argus_quant.validation) when the
        parallel track has populated packages/quant; otherwise uses a
        clearly-labeled internal expanding-window fallback. Never trains on
        future folds. Metric: 'brier' (default) or 'auc'.
        """
        X = np.asarray(X, dtype=float)
        y = np.asarray(y).ravel()
        n = len(y)
        if min_train is None:
            min_train = max(20, n // (n_splits + 1))
        test_window = max(10, (n - min_train) // max(n_splits, 1))
        splitter = quant_attr("validation.walk_forward_splits")
        if QUANT_AVAILABLE and splitter is not None:
            all_folds = list(
                splitter(n, train_window=min_train, test_window=test_window,
                         expanding=True)
            )
            folds = all_folds[-n_splits:] if len(all_folds) >= n_splits else all_folds
            splitter_name = "argus_quant.validation.walk_forward_splits"
        else:
            folds = _fallback_expanding_folds(n, n_splits, min_train)
            splitter_name = (
                "internal_fallback_expanding_window "
                "(argus_quant.validation not available — NOT the quant splitter)"
            )
            log.warning("walk_forward_scores using internal fallback splitter")

        from sklearn.metrics import brier_score_loss, roc_auc_score

        scores: List[float] = []
        for tr, te in folds:
            est = clone(self)
            est.calibrate = False  # calibration needs its own OOF; skip per-fold
            est.fit(X[tr], y[tr])
            p = est.predict_proba(X[te])[:, 1]
            if metric == "auc":
                scores.append(float(roc_auc_score(y[te], p)))
            else:
                scores.append(float(brier_score_loss(y[te], p)))
        return {
            "model_version": VERSION,
            "metric": metric,
            "splitter": splitter_name,
            "n_splits": len(folds),
            "fold_scores": scores,
            "mean": float(np.mean(scores)),
            "std": float(np.std(scores)),
        }


def _fallback_expanding_folds(
    n: int, n_splits: int, min_train: Optional[int]
) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Internal expanding-window folds. FALLBACK ONLY — not quant logic."""
    if min_train is None:
        min_train = max(20, n // (n_splits + 1))
    folds = []
    # test blocks of equal size at the end; train expands from the start
    test_size = max(10, (n - min_train) // n_splits)
    for k in range(n_splits):
        test_end = n - k * test_size
        test_start = test_end - test_size
        train_end = test_start
        if train_end < min_train or test_start < 0:
            break
        folds.append(
            (np.arange(0, train_end), np.arange(test_start, test_end))
        )
    folds.reverse()  # chronological order
    if not folds:
        raise ValueError("not enough samples for walk-forward folds")
    return folds
