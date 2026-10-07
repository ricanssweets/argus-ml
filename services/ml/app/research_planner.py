"""Deterministic query planner for POST /api/v1/research (ARGUS Phase 6).

No LLM. A keyword/intent router maps question patterns to SELECT queries
over the research store (app.research_store — SQLite mirror of the
migrations' ``predictions`` / ``model_metrics_daily`` / ``research``
schemas).

Hard rules (API_SPEC.md):
  * The assistant answers ONLY from stored platform records.
  * Any numeric claim without a stored record -> "I don't have data for
    that." Refusal text carries NO numeric claims at all (not even
    thresholds echoed from the question).
  * Every numeric claim cites [{table, id, as_of}].
  * Nothing is ever invented: if a query returns no rows, the planner
    refuses and says what WOULD be needed.

Seeding honesty: seeded rows carry data_status='SYNTHETIC_FIXTURE' in the
store. Answers built from them say so in plain text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from app import research_store

REFUSAL = "I don't have data for that."
SYNTHETIC_NOTE = ("NOTE: the records below are SYNTHETIC FIXTURES "
                  "(data_status=SYNTHETIC_FIXTURE), seeded for testing the "
                  "planner. They are not real market data or real analysis.")

MAX_HORIZON_DAYS = 20


@dataclass
class PlanResult:
    answer: str
    citations: List[Dict[str, str]] = field(default_factory=list)
    sql_used: Optional[str] = None
    data_status: str = "MISSING"  # API DataStatus; SYNTHETIC_FIXTURE is a
    # stored-row label, mapped to the API literal separately.


def _cite(table: str, row: Dict) -> Dict[str, str]:
    return {"table": table, "id": str(row["id"]),
            "as_of": str(row.get("as_of") or row.get("day") or
                         row.get("created_at") or "")}


def _synthetic(rows: List[Dict]) -> bool:
    return any(r.get("data_status") == "SYNTHETIC_FIXTURE"
               or r.get("data_status_overall") == "SYNTHETIC_FIXTURE"
               for r in rows)


def _maybe_note(rows: List[Dict]) -> str:
    return f" {SYNTHETIC_NOTE}" if _synthetic(rows) else ""


def _latest_asof(db: Optional[str] = None) -> Optional[str]:
    rows = research_store.query(
        "SELECT MAX(as_of) AS m FROM predictions", path=db)
    return rows[0]["m"] if rows and rows[0]["m"] else None


def _refuse(needed: str, sql: Optional[str] = None) -> PlanResult:
    # No digits anywhere in refusal text: a refusal must carry no claims.
    return PlanResult(
        answer=(f"{REFUSAL} {needed} I only answer from stored platform "
                "records (predictions, prediction outcomes, backtests, "
                "observatory metrics) and I will not invent numbers."),
        citations=[], sql_used=sql, data_status="MISSING")


# ------------------------------------------------------------------ intent 1
def _top_outperformers(q: str, db: Optional[str]) -> Optional[PlanResult]:
    if not re.search(r"outperform|highest probabil|top (s&p|stocks)|s&p ?500.*(probab|outperform)", q):
        return None
    asof = _latest_asof(db)
    sql = ("SELECT p.*, a.ticker, a.name FROM predictions p "
           "JOIN assets a ON a.id = p.asset_id "
           "WHERE p.horizon = 20 AND p.as_of = ? AND a.sp500 = 1 "
           "AND a.asset_type = 'STOCK' ORDER BY p.p_positive DESC LIMIT 5")
    rows = research_store.query(sql, (asof,), path=db) if asof else []
    if not rows:
        return _refuse(
            "To answer this I would need locked prediction records for "
            "large-cap index constituent stocks at the requested horizon "
            "in the predictions table.", sql)
    lines = [f"From stored predictions (as_of {asof}), the S&P 500 stocks "
             "with the highest probability of a positive return over the "
             "next twenty trading days are:"]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i}. {r['ticker']} ({r['name']}): p_positive "
            f"{r['p_positive'] * 100:.0f}%, expected return "
            f"{r['expected_return'] * 100:.1f}%, composite score "
            f"{r['composite_score']:.0f}, model {r['model_version']}.")
    lines.append(_maybe_note(rows).strip())
    return PlanResult(
        answer=" ".join(s for s in lines if s),
        citations=[_cite("predictions", r) for r in rows],
        sql_used=sql, data_status="SYNTHETIC_FIXTURE")


# ------------------------------------------------------------------ intent 2
_PE_CAP = 25.0  # "acceptable valuation" rule, stated in the answer.


def _etf_momentum_valuation(q: str, db: Optional[str]) -> Optional[PlanResult]:
    if not re.search(r"etf", q) or not re.search(
            r"momentum|strongest", q):
        return None
    asof = _latest_asof(db)
    sql = ("SELECT p.*, a.ticker, a.name, a.pe_ratio FROM predictions p "
           "JOIN assets a ON a.id = p.asset_id "
           "WHERE p.horizon = 20 AND p.as_of = ? AND a.asset_type = 'ETF' "
           "ORDER BY p.momentum_score DESC")
    rows = research_store.query(sql, (asof,), path=db) if asof else []
    if not rows:
        return _refuse(
            "To answer this I would need locked prediction records for "
            "ETFs, with momentum scores and point-in-time valuation ratios.",
            sql)
    ok = [r for r in rows if r["pe_ratio"] is not None
          and r["pe_ratio"] < _PE_CAP]
    dropped = [r for r in rows if r not in ok]
    lines = ["From stored predictions (as_of %s), ETFs ranked by momentum "
             "score, keeping only those with acceptable valuation (P/E "
             "below %.0f, a fixed screening rule):" % (asof, _PE_CAP)]
    for i, r in enumerate(ok, 1):
        lines.append(
            f"{i}. {r['ticker']} ({r['name']}): momentum score "
            f"{r['momentum_score']:.2f}, P/E {r['pe_ratio']:.1f}, "
            f"p_positive {r['p_positive'] * 100:.0f}%, composite "
            f"{r['composite_score']:.0f}.")
    if dropped:
        lines.append("Excluded on valuation: " + ", ".join(
            f"{r['ticker']} (P/E {r['pe_ratio']:.1f})" for r in dropped)
            + ".")
    lines.append(_maybe_note(rows).strip())
    return PlanResult(
        answer=" ".join(s for s in lines if s),
        citations=[_cite("predictions", r) for r in rows],
        sql_used=sql, data_status="SYNTHETIC_FIXTURE")


# ------------------------------------------------------------------ intent 3
def _high_prob_risk_reward(q: str, db: Optional[str]) -> Optional[PlanResult]:
    # Distinctive tokens: an explicit probability threshold (70%) and an
    # explicit risk/reward ratio (3:1). A bare "probability of
    # outperforming" belongs to the top_outperformers intent.
    if not re.search(r"70\s*%|3\s*:\s*1", q):
        return None
    asof = _latest_asof(db)
    sql = ("SELECT p.*, a.ticker, a.name FROM predictions p "
           "JOIN assets a ON a.id = p.asset_id "
           "WHERE p.horizon = 20 AND p.as_of = ? AND p.p_positive >= 0.70 "
           "AND p.risk_reward >= 3.0 ORDER BY p.p_positive DESC")
    rows = research_store.query(sql, (asof,), path=db) if asof else []
    if not rows:
        return _refuse(
            "To answer this I would need locked prediction records with "
            "p_positive and risk_reward fields at the requested horizon.",
            sql)
    lines = [f"From stored predictions (as_of {asof}), records with "
             "p_positive at or above seventy percent AND risk/reward at or "
             "above three to one:"]
    for r in rows:
        lines.append(
            f"{r['ticker']} ({r['name']}): p_positive "
            f"{r['p_positive'] * 100:.0f}%, risk/reward "
            f"{r['risk_reward']:.1f}, expected return "
            f"{r['expected_return'] * 100:.1f}%, composite score "
            f"{r['composite_score']:.0f}.")
    lines.append(_maybe_note(rows).strip())
    return PlanResult(
        answer=" ".join(s for s in lines if s),
        citations=[_cite("predictions", r) for r in rows],
        sql_used=sql, data_status="SYNTHETIC_FIXTURE")


# ------------------------------------------------------------------ intent 4
_ENGINE_NAMES = {
    "A": "Technical", "B": "Momentum", "C": "Fundamental", "D": "Macro",
    "E": "Regime", "F": "Options", "G": "Sentiment", "H": "Cross-asset",
}


_STOPWORDS = {"WHY", "DID", "THE", "HAS", "HAVE", "ITS", "SCORE",
               "FELL", "FALL", "DROP", "DROPPED", "DECLINED", "TODAY",
               "PREDICTION", "STOCK", "ETF"}


def _extract_ticker(q: str, db: Optional[str]) -> Optional[str]:
    """Find a known asset ticker mentioned in the question.

    Prefer real tickers from the assets table over a bare regex, so words
    like "WHY" are never mistaken for a ticker.
    """
    try:
        known = [r["ticker"] for r in research_store.query(
            "SELECT ticker FROM assets", path=db)]
    except Exception:
        known = []
    words = re.findall(r"[A-Z]{1,6}", q.upper())
    for w in words:
        if w in known:
            return w
    for w in words:
        if w not in _STOPWORDS and 1 <= len(w) <= 5:
            return w
    return None


def _why_score_fell(q: str, db: Optional[str]) -> Optional[PlanResult]:
    if not re.search(r"why|fell|fall|drop|declin|down", q):
        return None
    ticker = _extract_ticker(q, db)
    if not ticker:
        return None
    sql = ("SELECT p.*, a.ticker FROM predictions p "
           "JOIN assets a ON a.id = p.asset_id "
           "WHERE a.ticker = ? AND p.horizon = 20 "
           "ORDER BY p.as_of DESC LIMIT 2")
    rows = research_store.query(sql, (ticker,), path=db)
    if not rows:
        return _refuse(
            f"To answer this I would need locked prediction records for "
            f"{ticker} in the predictions table.", sql)
    if len(rows) == 1:
        r = rows[0]
        return PlanResult(
            answer=(f"I have only one locked prediction for {ticker} "
                    f"(as_of {r['as_of']}, composite score "
                    f"{r['composite_score']:.0f}) — with a single record "
                    "there is no previous prediction to diff against, so I "
                    "cannot attribute a score change." + _maybe_note(rows)),
            citations=[_cite("predictions", r)], sql_used=sql,
            data_status="SYNTHETIC_FIXTURE")
    cur, prev = rows[0], rows[1]
    d_score = cur["composite_score"] - prev["composite_score"]
    d_p = cur["p_positive"] - prev["p_positive"]
    cur_e = research_store.engine_signals_of(cur)
    prev_e = research_store.engine_signals_of(prev)
    deltas = sorted(
        ((e, cur_e.get(e, 0.0) - prev_e.get(e, 0.0))
         for e in _ENGINE_NAMES if e in cur_e or e in prev_e),
        key=lambda t: t[1])
    parts = [f"The latest locked prediction for {ticker} (as_of "
             f"{cur['as_of']}) has composite score "
             f"{cur['composite_score']:.0f}, versus "
             f"{prev['composite_score']:.0f} on {prev['as_of']} — a change "
             f"of {d_score:+.0f} points. p_positive moved from "
             f"{prev['p_positive'] * 100:.0f}% to "
             f"{cur['p_positive'] * 100:.0f}% ({d_p * 100:+.1f} percentage "
             "points)."]
    if deltas:
        parts.append("Per-engine signal changes (largest drags first): " +
                     "; ".join(
                         f"engine {_ENGINE_NAMES[e]} ({e}): {d:+.2f}"
                         for e, d in deltas) + ".")
    parts.append("This is a deterministic diff of the two locked records — "
                 "not a causal explanation of the market." + _maybe_note(rows))
    return PlanResult(
        answer=" ".join(parts),
        citations=[_cite("predictions", cur), _cite("predictions", prev)],
        sql_used=sql, data_status="SYNTHETIC_FIXTURE")


# ------------------------------------------------------------------ intent 5
def _sector_rotation(q: str, db: Optional[str]) -> Optional[PlanResult]:
    if not re.search(r"sector|rotation|rotat", q):
        return None
    asofs = research_store.query(
        "SELECT DISTINCT as_of FROM predictions ORDER BY as_of DESC LIMIT 2",
        path=db)
    if len(asofs) < 2:
        return _refuse(
            "To answer this I would need at least two prediction snapshots "
            "with sector coverage in the predictions table.", None)
    latest, prev = asofs[0]["as_of"], asofs[1]["as_of"]
    sql = ("SELECT a.sector, "
           "AVG(CASE WHEN p.as_of = ? THEN p.momentum_score END) AS m_new, "
           "AVG(CASE WHEN p.as_of = ? THEN p.momentum_score END) AS m_old, "
           "COUNT(CASE WHEN p.as_of = ? THEN 1 END) AS n_new, "
           "COUNT(CASE WHEN p.as_of = ? THEN 1 END) AS n_old, "
           "GROUP_CONCAT(CASE WHEN p.as_of = ? THEN p.id END) AS ids "
           "FROM predictions p JOIN assets a ON a.id = p.asset_id "
           "WHERE p.horizon = 20 AND p.as_of IN (?, ?) AND a.sector IS NOT "
           "NULL GROUP BY a.sector")
    rows = research_store.query(
        sql, (latest, prev, latest, prev, latest, latest, prev), path=db)
    rows = [r for r in rows if r["m_new"] is not None
            and r["m_old"] is not None and r["n_new"] > 0 and r["n_old"] > 0]
    if not rows:
        return _refuse(
            "To answer this I would need prediction snapshots with sector "
            "coverage and momentum scores.", sql)
    ranked = sorted(rows, key=lambda r: r["m_new"] - r["m_old"],
                    reverse=True)
    inflow, outflow = ranked[0], ranked[-1]
    d_in, d_out = inflow["m_new"] - inflow["m_old"], outflow["m_new"] - outflow["m_old"]
    lines = [f"Comparing stored momentum scores between {prev} and {latest}, "
             "sector-aggregated capital rotation looks like this:"]
    for r in ranked:
        d = r["m_new"] - r["m_old"]
        lines.append(f"{r['sector']}: avg momentum {r['m_old']:.2f} -> "
                     f"{r['m_new']:.2f} ({d:+.2f}, {r['n_new']} names).")
    lines.append(f"Strongest inflow: {inflow['sector']} ({d_in:+.2f}); "
                 f"strongest outflow: {outflow['sector']} ({d_out:+.2f}). "
                 "Rotation is inferred from signal changes only — it is not "
                 "a measured flow of funds." + _maybe_note(rows))
    cites = []
    for r in ranked:
        for pid in (r["ids"] or "").split(","):
            if pid:
                cites.append({"table": "predictions", "id": pid,
                              "as_of": latest})
    return PlanResult(answer=" ".join(lines), citations=cites, sql_used=sql,
                      data_status="SYNTHETIC_FIXTURE")


# ------------------------------------------------------------------ intent 6
def _bear_market_accuracy(q: str, db: Optional[str]) -> Optional[PlanResult]:
    if not re.search(r"bear", q) or not re.search(
            r"accura|model|perform", q):
        return None
    sql = ("SELECT COUNT(*) AS days, SUM(n) AS n_total, "
           "AVG(accuracy) AS acc, AVG(brier) AS br, AVG(roc_auc) AS auc "
           "FROM model_metrics_daily "
           "WHERE regime = 'Bear' AND model_version = 'ARGUS-EQ-1.0' "
           "AND horizon = 20")
    rows = research_store.query(sql, path=db)
    agg = rows[0] if rows else {}
    detail_sql = ("SELECT day, n, accuracy, brier FROM model_metrics_daily "
                  "WHERE regime = 'Bear' AND model_version = 'ARGUS-EQ-1.0' "
                  "AND horizon = 20 ORDER BY day DESC LIMIT 5")
    detail = research_store.query(detail_sql, path=db)
    n_total = agg.get("n_total") or 0
    if not agg.get("days") or n_total < 20:
        return _refuse(
            "To answer this I would need observatory rows for the model in "
            "bear-market regimes, with at least twenty scored predictions.",
            sql)
    lines = [f"From the stored observatory table, ARGUS-EQ-1.0 at horizon "
             f"twenty has {agg['days']} bear-regime daily rows covering "
             f"{n_total} scored predictions: mean directional accuracy "
             f"{agg['acc'] * 100:.1f}%, mean Brier score {agg['br']:.3f}, "
             f"mean ROC-AUC {agg['auc']:.3f}."]
    if detail:
        lines.append("Most recent bear-regime days: " + "; ".join(
            f"{r['day'][:10]}: n={r['n']}, accuracy "
            f"{r['accuracy'] * 100:.1f}%, Brier {r['brier']:.3f}"
            for r in detail) + ".")
    lines.append("Sample size note: bear-regime history is thin, so treat "
                 "these aggregates as indicative, not conclusive."
                 + _maybe_note(detail or rows))
    cites = [{"table": "model_metrics_daily", "id": f"ARGUS-EQ-1.0/20/Bear",
              "as_of": detail[0]["day"] if detail else ""}]
    return PlanResult(answer=" ".join(lines), citations=cites, sql_used=sql,
                      data_status="SYNTHETIC_FIXTURE")


# ------------------------------------------------------------ router / entry
_INTENTS: List[Tuple[str, Callable[[str, Optional[str]], Optional[PlanResult]]]] = [
    ("why_score_fell", _why_score_fell),
    ("bear_market_accuracy", _bear_market_accuracy),
    ("sector_rotation", _sector_rotation),
    ("high_prob_risk_reward", _high_prob_risk_reward),
    ("etf_momentum_valuation", _etf_momentum_valuation),
    ("top_outperformers", _top_outperformers),
]


def answer(question: str, db: Optional[str] = None) -> PlanResult:
    """Plan and answer a research question from stored records only."""
    q = question.strip().lower()
    if not q:
        return _refuse("To answer this I would need a question with a "
                       "recognizable topic.")
    try:
        research_store.init_schema(db)  # ensure tables exist; no-op if so
        for _name, handler in _INTENTS:
            try:
                res = handler(q, db)
            except Exception:
                res = None  # a handler bug must not invent an answer
            if res is not None:
                return res
    except Exception:
        pass
    return _refuse(
        "I do not recognize a supported question pattern. I can answer "
        "the six brief questions: top outperformers, ETF momentum with "
        "valuation, high-probability high risk/reward setups, why a "
        "ticker's score changed, sector rotation, and bear-market "
        "accuracy — all from stored records only.")
