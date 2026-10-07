"""Evaluation metrics. Metric hierarchy (locked law): calibration first,
then discrimination, then accuracy, then error magnitude.

``MIN_SAMPLES_PER_BUCKET = 20``: any calibration bucket or performance claim
needs >= 20 samples. ``calibration_table`` merges sub-20 buckets with
neighbors and flags anything still below 20 as insufficient.
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import (accuracy_score, f1_score, log_loss,
                             precision_score, recall_score, roc_auc_score)

MIN_SAMPLES_PER_BUCKET = 20
EPS = 1e-12


def _as_arrays(y_true, y_prob):
    y_true = np.asarray(y_true, dtype=float).ravel()
    y_prob = np.asarray(y_prob, dtype=float).ravel()
    if len(y_true) != len(y_prob):
        raise ValueError("y_true and y_prob must have the same length")
    if len(y_true) == 0:
        raise ValueError("empty inputs")
    return y_true, np.clip(y_prob, EPS, 1.0 - EPS)


def brier_score(y_true, y_prob) -> float:
    """Mean squared error of predicted probabilities vs 0/1 labels."""
    y_true, y_prob = _as_arrays(y_true, y_prob)
    return float(np.mean((y_prob - y_true) ** 2))


def logloss(y_true, y_prob) -> float:
    """Binary log-loss (probabilities clipped to [1e-12, 1-1e-12])."""
    y_true, y_prob = _as_arrays(y_true, y_prob)
    return float(log_loss(y_true, y_prob, labels=[0, 1]))


def roc_auc(y_true, y_prob) -> float:
    """ROC-AUC. Returns NaN when only one class is present (undefined)."""
    y_true, y_prob = _as_arrays(y_true, y_prob)
    if len(np.unique(y_true)) < 2:
        return float("nan")
    return float(roc_auc_score(y_true, y_prob))


def accuracy_precision_recall_f1(y_true, y_prob,
                                 threshold: float = 0.5) -> dict:
    """Binary classification metrics at the operating threshold."""
    y_true, y_prob = _as_arrays(y_true, y_prob)
    pred = (y_prob >= threshold).astype(int)
    yt = y_true.astype(int)
    return {
        "accuracy": float(accuracy_score(yt, pred)),
        "precision": float(precision_score(yt, pred, zero_division=0)),
        "recall": float(recall_score(yt, pred, zero_division=0)),
        "f1": float(f1_score(yt, pred, zero_division=0)),
        "threshold": threshold,
        "n": len(yt),
    }


def mae(y_true, y_pred) -> float:
    y_true = np.asarray(y_true, dtype=float).ravel()
    y_pred = np.asarray(y_pred, dtype=float).ravel()
    return float(np.mean(np.abs(y_true - y_pred)))


def rmse(y_true, y_pred) -> float:
    y_true = np.asarray(y_true, dtype=float).ravel()
    y_pred = np.asarray(y_pred, dtype=float).ravel()
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def directional_accuracy(y_true_returns, y_pred_returns) -> float:
    """Fraction of periods where sign(pred) == sign(true). Zero true return
    counts as correct only if prediction is exactly 0."""
    yt = np.asarray(y_true_returns, dtype=float).ravel()
    yp = np.asarray(y_pred_returns, dtype=float).ravel()
    return float(np.mean(np.sign(yt) == np.sign(yp)))


def calibration_table(y_true, y_prob, n_bins: int = 10) -> dict:
    """Reliability table with the MIN_SAMPLES_PER_BUCKET rule enforced.

    Equal-width bins over [0,1]. Bins with < 20 samples are merged with the
    adjacent bin holding fewer samples (repeated until every bin has >= 20
    or only one bin remains). A single remaining bin with < 20 samples, or
    any bin that cannot be merged to 20, is reported with
    ``"insufficient": True`` and excluded from the calibration error.

    Returns {"buckets": [...], "calibration_error": float,
             "insufficient_bins": [...], "n": int}.
    calibration_error = sum(n_b/N * |pred_mean - obs_mean|) over sufficient
    buckets only.
    """
    y_true, y_prob = _as_arrays(y_true, y_prob)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bins: list[dict] = []
    for b in range(n_bins):
        lo, hi = edges[b], edges[b + 1]
        mask = (y_prob > lo) | ((b == 0) & (y_prob == lo))
        mask &= y_prob <= hi
        idx = np.flatnonzero(mask)
        bins.append({"lo": float(lo), "hi": float(hi), "idx": idx})

    def merge_into(bins, i, j):
        bins[i]["idx"] = np.concatenate([bins[i]["idx"], bins[j]["idx"]])
        bins[i]["lo"] = min(bins[i]["lo"], bins[j]["lo"])
        bins[i]["hi"] = max(bins[i]["hi"], bins[j]["hi"])
        del bins[j]

    # Merge small bins into the smaller neighbor until all >= 20 or 1 left.
    changed = True
    while changed and len(bins) > 1:
        changed = False
        for i, b in enumerate(bins):
            if len(b["idx"]) < MIN_SAMPLES_PER_BUCKET:
                # merge into the adjacent bin with fewer samples
                if i == 0:
                    j = 1
                elif i == len(bins) - 1:
                    j = i - 1
                else:
                    j = i - 1 if len(bins[i - 1]["idx"]) <= len(bins[i + 1]["idx"]) else i + 1
                if j < i:
                    merge_into(bins, j, i)
                else:
                    merge_into(bins, i, j)
                changed = True
                break

    N = len(y_true)
    buckets, insufficient = [], []
    cal_err = 0.0
    for b in bins:
        idx = b["idx"]
        n_b = len(idx)
        pred_mean = float(np.mean(y_prob[idx])) if n_b else float("nan")
        obs_mean = float(np.mean(y_true[idx])) if n_b else float("nan")
        ok = n_b >= MIN_SAMPLES_PER_BUCKET
        buckets.append({"bin_low": round(b["lo"], 4), "bin_high": round(b["hi"], 4),
                        "n": n_b, "predicted_mean": round(pred_mean, 4),
                        "observed_mean": round(obs_mean, 4),
                        "insufficient": not ok})
        if ok:
            cal_err += (n_b / N) * abs(pred_mean - obs_mean)
        else:
            insufficient.append({"bin_low": round(b["lo"], 4),
                                 "bin_high": round(b["hi"], 4), "n": n_b})
    return {"buckets": buckets, "calibration_error": round(cal_err, 6),
            "insufficient_bins": insufficient, "n": N,
            "min_samples_per_bucket": MIN_SAMPLES_PER_BUCKET}


def reliability_curve_data(y_true, y_prob, n_bins: int = 10) -> dict:
    """Data for plotting the reliability curve: sufficient buckets only."""
    table = calibration_table(y_true, y_prob, n_bins=n_bins)
    pts = [{"x": b["predicted_mean"], "y": b["observed_mean"], "n": b["n"]}
           for b in table["buckets"] if not b["insufficient"]]
    return {"points": pts,
            "diagonal": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}],
            "calibration_error": table["calibration_error"],
            "insufficient_bins": table["insufficient_bins"]}
