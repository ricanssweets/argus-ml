"""Alert rule evaluation — pure functions ``(rule, context) -> alert | None``.

Matches the ``alert_rules`` table shape (db/migrations/005_portfolio_signals.sql):
rule = {"id"?, "rule_type": str, "ticker": str | None, "params": dict,
        "is_active": bool}.

Context carries point-in-time inputs only (nothing after ``as_of``):
    {
      "as_of": str (ISO-8601),
      "ticker": str,
      "bars": pd.DataFrame [time, open, high, low, close, volume],
      "prev_pred": dict | None,   # previous locked prediction record
      "curr_pred": dict | None,   # latest locked prediction record
      "options_unusual": bool,    # engine-F unusual-activity flag
      "earnings_date": str | None (ISO date),
      "sentiment_velocity": float | None,
      "regime_prev": str | None,
      "regime_curr": str | None,
    }

A rule fires -> returns an alert dict in the ``alerts`` table shape;
otherwise returns None. All math is trailing-window only (no lookahead).

Rule types:
  score_change    |Δ composite_score| >= params.threshold (default 10)
  prob_change     |Δ p_positive| >= params.threshold (default 0.10)
  breakout        close > 20d high (excluding today) AND volume z-score
                  >= params.min_vol_z (default 1.5)
  earnings        earnings_date within params.days_ahead (default 7) of as_of
  unusual_volume  volume > params.mult (default 3.0) x 20d avg volume
  unusual_options options_unusual flag set (engine F)
  vol_spike       ATR(14) z-score over trailing 20 >= params.threshold (2.0)
  regime_change   regime_prev != regime_curr (both present)
  news            sentiment_velocity >= params.threshold (default 2.0)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, Optional

import numpy as np
import pandas as pd

DEFAULTS: Dict[str, Dict[str, Any]] = {
    "score_change": {"threshold": 10.0},
    "prob_change": {"threshold": 0.10},
    "breakout": {"lookback": 20, "min_vol_z": 1.5},
    "earnings": {"days_ahead": 7},
    "unusual_volume": {"mult": 3.0, "lookback": 20},
    "unusual_options": {},
    "vol_spike": {"threshold": 2.0, "lookback": 20},
    "regime_change": {},
    "news": {"threshold": 2.0},
}

RULE_TYPES = sorted(DEFAULTS)


def _bars(ctx: Dict[str, Any]) -> Optional[pd.DataFrame]:
    b = ctx.get("bars")
    if b is None or len(b) < 25:
        return None
    return b


def _alert(rule: Dict[str, Any], ctx: Dict[str, Any], title: str,
           body: str, severity: str = "INFO") -> Dict[str, Any]:
    sev = (rule.get("params") or {}).get("severity", severity)
    return {
        "id": None,  # assigned when persisted
        "created_at": ctx.get("as_of"),
        "alert_type": rule["rule_type"],
        "ticker": rule.get("ticker") or ctx.get("ticker"),
        "title": title,
        "body": body,
        "severity": sev,
        "channel": "IN_APP",
        "rule_id": rule.get("id"),
    }


def rule_score_change(rule: Dict, ctx: Dict) -> Optional[Dict]:
    p, c = ctx.get("prev_pred"), ctx.get("curr_pred")
    if not p or not c:
        return None
    thr = float((rule.get("params") or {}).get(
        "threshold", DEFAULTS["score_change"]["threshold"]))
    delta = float(c["composite_score"]) - float(p["composite_score"])
    if abs(delta) < thr:
        return None
    direction = "rose" if delta > 0 else "fell"
    return _alert(rule, ctx,
                  f"{ctx.get('ticker')}: prediction score {direction} "
                  f"{abs(delta):.0f} points",
                  f"Composite score moved from {p['composite_score']:.0f} to "
                  f"{c['composite_score']:.0f} (threshold {thr:.0f}). "
                  f"as_of {c.get('as_of')}.",
                  severity="WARN" if abs(delta) >= 2 * thr else "INFO")


def rule_prob_change(rule: Dict, ctx: Dict) -> Optional[Dict]:
    p, c = ctx.get("prev_pred"), ctx.get("curr_pred")
    if not p or not c:
        return None
    thr = float((rule.get("params") or {}).get(
        "threshold", DEFAULTS["prob_change"]["threshold"]))
    delta = float(c["p_positive"]) - float(p["p_positive"])
    if abs(delta) < thr:
        return None
    return _alert(
        rule, ctx,
        f"{ctx.get('ticker')}: p_positive moved {delta:+.1%}",
        f"p_positive {p['p_positive']:.0%} -> {c['p_positive']:.0%} "
        f"(threshold {thr:.0%}). as_of {c.get('as_of')}.",
        severity="WARN")


def rule_breakout(rule: Dict, ctx: Dict) -> Optional[Dict]:
    df = _bars(ctx)
    if df is None:
        return None
    prm = rule.get("params") or {}
    lb = int(prm.get("lookback", DEFAULTS["breakout"]["lookback"]))
    min_z = float(prm.get("min_vol_z", DEFAULTS["breakout"]["min_vol_z"]))
    closes = df["close"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    vols = df["volume"].to_numpy(dtype=float)
    if len(closes) < lb + 1:
        return None
    prev_high = float(np.max(highs[-(lb + 1):-1]))
    last_close = float(closes[-1])
    if last_close <= prev_high:
        return None
    v_mean = float(np.mean(vols[-(lb + 1):-1]))
    v_std = float(np.std(vols[-(lb + 1):-1]))
    z = (float(vols[-1]) - v_mean) / v_std if v_std > 0 else 0.0
    if z < min_z:
        return None
    return _alert(
        rule, ctx,
        f"{ctx.get('ticker')}: breakout above {lb}d high",
        f"Close {last_close:.2f} > prior {lb}d high {prev_high:.2f} with "
        f"volume z-score {z:.1f} (min {min_z:.1f}).",
        severity="WARN")


def rule_earnings(rule: Dict, ctx: Dict) -> Optional[Dict]:
    ed = ctx.get("earnings_date")
    as_of = ctx.get("as_of")
    if not ed or not as_of:
        return None
    days = int((rule.get("params") or {}).get(
        "days_ahead", DEFAULTS["earnings"]["days_ahead"]))
    try:
        delta_days = (datetime.fromisoformat(ed[:10]) -
                      datetime.fromisoformat(as_of[:10])).days
    except ValueError:
        return None
    if not 0 <= delta_days <= days:
        return None
    return _alert(
        rule, ctx,
        f"{ctx.get('ticker')}: earnings in {delta_days}d",
        f"Earnings date {ed[:10]} is {delta_days} calendar days after "
        f"as_of {as_of[:10]} (window {days}d).")


def rule_unusual_volume(rule: Dict, ctx: Dict) -> Optional[Dict]:
    df = _bars(ctx)
    if df is None:
        return None
    prm = rule.get("params") or {}
    mult = float(prm.get("mult", DEFAULTS["unusual_volume"]["mult"]))
    lb = int(prm.get("lookback", DEFAULTS["unusual_volume"]["lookback"]))
    vols = df["volume"].to_numpy(dtype=float)
    avg = float(np.mean(vols[-(lb + 1):-1]))
    if avg <= 0:
        return None
    ratio = float(vols[-1]) / avg
    if ratio < mult:
        return None
    return _alert(
        rule, ctx,
        f"{ctx.get('ticker')}: unusual volume ({ratio:.1f}x avg)",
        f"Volume {vols[-1]:,.0f} is {ratio:.1f}x the {lb}d average "
        f"{avg:,.0f} (threshold {mult:.1f}x).",
        severity="WARN")


def rule_unusual_options(rule: Dict, ctx: Dict) -> Optional[Dict]:
    if not ctx.get("options_unusual"):
        return None
    return _alert(
        rule, ctx,
        f"{ctx.get('ticker')}: unusual options activity",
        "Engine F flagged unusual options flow at as_of "
        f"{ctx.get('as_of')}.",
        severity="WARN")


def _atr14(df: pd.DataFrame) -> np.ndarray:
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    tr = np.maximum(h[1:] - l[1:], np.abs(h[1:] - c[:-1]))
    atr = np.full(len(df), np.nan)
    for i in range(14, len(df)):
        atr[i] = np.mean(tr[i - 14:i])
    return atr


def rule_vol_spike(rule: Dict, ctx: Dict) -> Optional[Dict]:
    df = _bars(ctx)
    if df is None:
        return None
    thr = float((rule.get("params") or {}).get(
        "threshold", DEFAULTS["vol_spike"]["threshold"]))
    lb = int((rule.get("params") or {}).get(
        "lookback", DEFAULTS["vol_spike"]["lookback"]))
    atr = _atr14(df)
    window = atr[-(lb + 1):-1]
    if np.isnan(window).any() or float(np.std(window)) == 0:
        return None
    z = (float(atr[-1]) - float(np.mean(window))) / float(np.std(window))
    if z < thr:
        return None
    return _alert(
        rule, ctx,
        f"{ctx.get('ticker')}: volatility spike (ATR z={z:.1f})",
        f"ATR(14) {atr[-1]:.2f} is {z:.1f} std above its {lb}d mean "
        f"(threshold {thr:.1f}).",
        severity="WARN")


def rule_regime_change(rule: Dict, ctx: Dict) -> Optional[Dict]:
    prev, curr = ctx.get("regime_prev"), ctx.get("regime_curr")
    if not prev or not curr or prev == curr:
        return None
    return _alert(
        rule, ctx,
        f"Market regime changed: {prev} -> {curr}",
        f"Regime classification moved from {prev} to {curr} at "
        f"{ctx.get('as_of')}.",
        severity="WARN")


def rule_news(rule: Dict, ctx: Dict) -> Optional[Dict]:
    sv = ctx.get("sentiment_velocity")
    if sv is None:
        return None
    thr = float((rule.get("params") or {}).get(
        "threshold", DEFAULTS["news"]["threshold"]))
    if float(sv) < thr:
        return None
    return _alert(
        rule, ctx,
        f"{ctx.get('ticker')}: news sentiment velocity spike",
        f"Sentiment velocity {sv:.1f} >= threshold {thr:.1f} at "
        f"{ctx.get('as_of')}.",
        severity="WARN")


_EVALUATORS: Dict[str, Callable[[Dict, Dict], Optional[Dict]]] = {
    "score_change": rule_score_change,
    "prob_change": rule_prob_change,
    "breakout": rule_breakout,
    "earnings": rule_earnings,
    "unusual_volume": rule_unusual_volume,
    "unusual_options": rule_unusual_options,
    "vol_spike": rule_vol_spike,
    "regime_change": rule_regime_change,
    "news": rule_news,
}


def evaluate_rule(rule: Dict[str, Any], context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Evaluate one rule against a point-in-time context.

    Returns an alert dict (alerts table shape) or None. Inactive rules and
    unknown rule types never fire. Exceptions are swallowed -> None: a
    broken rule must not crash the cron evaluator.
    """
    if not rule.get("is_active", True):
        return None
    fn = _EVALUATORS.get(rule.get("rule_type", ""))
    if fn is None:
        return None
    try:
        return fn(rule, context)
    except Exception:
        return None


def evaluate_rules(rules: list, context: Dict[str, Any]) -> list:
    """Evaluate many rules; return the alerts that fired."""
    out = []
    for r in rules:
        a = evaluate_rule(r, context)
        if a is not None:
            out.append(a)
    return out
