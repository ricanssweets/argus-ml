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
                        step: int = 1, expanding: bool = True,
                        purge: int = 1, embargo: int = 0):
    """Yield ``(train_idx, test_idx)`` integer arrays.

    Walk-forward: train on ``[t-train_window, t)`` (or ``[0, t)`` when
    expanding), test on ``[t, t+test_window)``, then step ``t`` forward.
    ``n`` is the number of rows. Pure index arithmetic - no data touched.

    PURGE / EMBARGO (label-overlap leak, see the module docstring): the
    label for as-of ``i`` with horizon ``h`` reads prices in ``(i, i+h]``, so
    a training row at ``i = t-1`` with ``h = 20`` already sees 19 bars of
    the test window. ``purge`` drops the ``purge`` rows immediately BEFORE
    the test block (training ends at ``t - purge``); callers whose label
    horizon is ``h`` must pass ``purge=h`` (the default ``1`` is the
    minimum honest gap and is exact for 1-bar labels). ``embargo`` drops
    ``embargo`` further rows at the end of the training block, absorbing
    serial correlation within the training data itself; the two are
    additive, so training ends at ``t - purge - embargo``.

    There is deliberately no POST-test embargo: walk-forward training never
    contains rows after ``t``, so an embargo after the test window would be
    a no-op (unlike :func:`purged_kfold_splits`, where training surrounds
    the test fold on both sides).

    Folds whose purged training block would be empty are skipped.
    """
    if train_window <= 0 or test_window <= 0 or step <= 0:
        raise ValueError("windows and step must be positive")
    if purge < 0 or embargo < 0:
        raise ValueError("purge and embargo must be >= 0")
    gap = int(purge) + int(embargo)
    t = train_window
    while t + test_window <= n:
        train_start = 0 if expanding else t - train_window
        train_end = t - gap
        if train_end > train_start:
            train = np.arange(train_start, train_end)
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
    if not 2 <= int(n_splits) <= n:
        raise ValueError(
            f"n_splits must satisfy 2 <= n_splits <= n ({n}), got {n_splits}")
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


def _is_number(x) -> bool:
    return isinstance(x, (int, float, np.integer, np.floating)) \
        and not isinstance(x, bool)


def _comparable(x, y):
    """Return ``(x, y)`` in a mutually comparable representation.

    ``label_windows_from_index`` returns integer POSITIONS while callers
    often hold Timestamp as-of values. Two numbers compare numerically and
    two timestamps compare as ``pd.Timestamp``; mixing a position with a
    timestamp has no common coordinate system, so it raises ``ValueError``
    (with the stable advice to use ``label_start_time``/``label_end_time``)
    instead of the raw ``TypeError`` the bare ``<`` produced.
    """
    if _is_number(x) and _is_number(y):
        return float(x), float(y)
    if _is_number(x) != _is_number(y):
        raise ValueError(
            "cannot compare a bar position with a timestamp "
            f"({x!r} vs {y!r}): use label_start_time/label_end_time for "
            "timestamp as-of values, or label_start/label_end for positions")
    try:
        return pd.Timestamp(x), pd.Timestamp(y)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"uncomparable values {x!r} and {y!r}: {exc}") from exc


def assert_no_leakage(X_times, y_window_starts, y_window_ends) -> None:
    """Raise ``AssertionError`` if any row violates the label discipline.

    For every row ``i``: ``X_times[i] < y_window_starts[i]`` must hold, i.e.
    the label window ``(start, end]`` begins strictly AFTER the feature
    as-of timestamp. Each row is compared in a common representation
    (:func:`_comparable`): two numbers compare numerically, two timestamps
    compare as ``pd.Timestamp``, and mixing a position with a timestamp
    raises ``ValueError`` naming the row (use ``label_start_time`` /
    ``label_end_time`` for timestamp as-of values) -- never a bare
    ``TypeError``.
    """
    X = list(X_times)
    S = list(y_window_starts)
    E = list(y_window_ends)
    if not (len(X) == len(S) == len(E)):
        raise ValueError("all inputs must have the same length")
    bad = []
    for i in range(len(X)):
        try:
            x, s = _comparable(X[i], S[i])
            _, e = _comparable(S[i], E[i])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"row {i}: cannot compare as_of={X[i]!r}, "
                f"label_window=({S[i]!r}, {E[i]!r}): {exc}") from exc
        if not (x < s <= e):
            bad.append(i)
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
    integer positions (compatible with the splitters above);
    ``label_start_time``/``label_end_time`` carry the same bounds as
    timestamps so the frame can be compared against timestamp as-of values
    without mixing types.
    """
    n = len(index)
    rows = []
    for i in range(0, n - horizon, step):
        rows.append({"as_of": index[i],
                     "label_start": i + 1, "label_end": i + horizon,
                     "label_start_time": index[i + 1],
                     "label_end_time": index[i + horizon],
                     "row": i})
    return pd.DataFrame(rows)
