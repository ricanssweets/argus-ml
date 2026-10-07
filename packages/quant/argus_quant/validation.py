"""Leakage-safe validation: walk-forward splits, purged K-fold with embargo,
and the anti-leakage assertion.

Label discipline (locked law): the label for as-of ``t`` and horizon ``h``
is a function of prices in ``(t, t+h]``. Features for ``t`` use only data
``<= t``. Enforced here by ``assert_no_leakage``: for every training row,
``max(feature_timestamp) <= as_of < min(label_timestamp)``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def walk_forward_splits(n: int, train_window: int, test_window: int,
                        step: int = 1, expanding: bool = True):
    """Yield ``(train_idx, test_idx)`` integer arrays.

    Walk-forward: train on ``[t-train_window, t)`` (or ``[0, t)`` when
    expanding), test on ``[t, t+test_window)``, then step ``t`` forward.
    ``n`` is the number of rows. Pure index arithmetic - no data touched.
    """
    if train_window <= 0 or test_window <= 0 or step <= 0:
        raise ValueError("windows and step must be positive")
    t = train_window
    while t + test_window <= n:
        train = np.arange(0, t) if expanding else np.arange(t - train_window, t)
        test = np.arange(t, t + test_window)
        yield train, test
        t += step


def purged_kfold_splits(n: int, n_splits: int,
                        y_window_starts: np.ndarray,
                        y_window_ends: np.ndarray,
                        embargo: int = 0, shuffle: bool = False,
                        seed: int = 42):
    """Purged K-fold with embargo (for pooled ML).

    * Purge: drop training rows whose label window ``[start, end]`` overlaps
      ANY test row's label window.
    * Embargo: drop training rows whose label window starts within
      ``embargo`` bars after the end of any test window.

    ``y_window_starts``/``y_window_ends`` are integer positions of each row's
    label window. Yields ``(train_idx, test_idx)`` with purged/embargoed
    training indices. Deterministic given ``seed`` (used only if shuffle).
    """
    starts = np.asarray(y_window_starts, dtype=np.int64)
    ends = np.asarray(y_window_ends, dtype=np.int64)
    if not (len(starts) == len(ends) == n):
        raise ValueError("window arrays must have length n")
    rng = np.random.default_rng(seed)
    idx = np.arange(n)
    if shuffle:
        rng.shuffle(idx)
    folds = np.array_split(idx, n_splits)
    for test in folds:
        t_min, t_max = starts[test].min(), ends[test].max()
        train_mask = np.ones(n, dtype=bool)
        train_mask[test] = False
        overlap = (starts <= t_max) & (ends >= t_min)      # purge
        in_embargo = (starts > t_max) & (starts <= t_max + embargo)
        train_mask &= ~(overlap | in_embargo)
        yield np.flatnonzero(train_mask), np.sort(test)


def assert_no_leakage(X_times, y_window_starts, y_window_ends) -> None:
    """Raise ``AssertionError`` if any row violates the label discipline.

    For every row ``i``: ``X_times[i] < y_window_starts[i]`` must hold, i.e.
    the label window ``(start, end]`` begins strictly AFTER the feature
    as-of timestamp. Timestamps may be ints, floats, or datetimes
    (compared pairwise; mixed types must be mutually comparable).
    """
    X = list(X_times)
    S = list(y_window_starts)
    E = list(y_window_ends)
    if not (len(X) == len(S) == len(E)):
        raise ValueError("all inputs must have the same length")
    bad = [i for i in range(len(X)) if not (X[i] < S[i] <= E[i])]
    if bad:
        detail = "; ".join(
            f"row {i}: as_of={X[i]}, label_window=({S[i]}, {E[i]}]"
            for i in bad[:5])
        raise AssertionError(
            f"LEAKAGE: {len(bad)} row(s) violate as_of < label_window_start. "
            f"First violations: {detail}")


def label_windows_from_index(index: pd.DatetimeIndex, horizon: int,
                             step: int = 1) -> pd.DataFrame:
    """Helper: build (as_of, label_start, label_end) rows for a bar index.

    Label for as-of bar ``i`` covers bars ``(i, i+horizon]``. Only rows with
    a full label window are returned. ``label_start``/``label_end`` are
    integer positions (compatible with the splitters above).
    """
    n = len(index)
    rows = []
    for i in range(0, n - horizon, step):
        rows.append({"as_of": index[i], "label_start": i + 1,
                     "label_end": i + horizon, "row": i})
    return pd.DataFrame(rows)
