"""Sentiment connector: deterministic finance-lexicon scorer.

Inputs: recent 8-K filing descriptions from EDGAR (via ``edgar.recent_filings``)
plus any headline list the caller passes in (each {"date", "text"}).
No ML models, no downloads -- a fixed word list scored deterministically,
so results are reproducible and auditable.

``sentiment_summary(cik, as_of, headlines=None)`` returns:
  polarity      mean document score in [-1, 1] over the lookback window
  momentum_28d  polarity(last 28d docs) - polarity(prior 28d docs)
  velocity_z    z-score of the 7d document count vs trailing 90d daily counts
  n_docs        number of scored documents
  docs          per-document {date, text, score} (audit trail)

Lookbacks: filings 180d (8-K are sparse; 8-K cadence is event-driven),
headlines 90d, momentum windows 28d/28d, velocity 7d vs 90d.

Assets with no coverage -> n_docs == 0; Engine G reports MISSING.
Confidence is penalized below 5 documents (documented in Engine G).
"""

from __future__ import annotations

import re

import pandas as pd

from . import edgar

_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")

# --- Documented finance lexicon -------------------------------------------
# Positive: beats, raises, upgrades, growth, buybacks, dividends, wins.
POSITIVE_WORDS = frozenset("""
beat beats beating exceeded exceeds exceeding outperform outperformed
outperforming upgrade upgraded upgrades outperformer raised raises raising
raise guidance raised-guidance increase increased increasing growth growing
grew record record-high all-time-high buyback buybacks repurchase dividend
dividends raised-dividend profit profitable profitability margin-expansion
expansion win wins won awarded contract-awarded backlog strength strong
robust solid momentum accelerate accelerating accelerated bullish optimism
optimistic confident confidence approve approved approval fda-approval
clearance settled settlement-win partnership partnered collaboration
acquire acquired acquisition merger synergies innovation innovative launch
launched milestone achieved surpass surpassed upside surprise positive
""".split())

# Negative: misses, cuts, downgrades, losses, fraud, restatements, layoffs.
NEGATIVE_WORDS = frozenset("""
miss misses missed missing below-miss downgrade downgraded downgrades
cut cuts cutting lowered lowers lowering reduce reduced guidance-cut
warning profit-warning loss losses losing unprofitable deficit restatement
restated restates fraud fraudulent investigation investigated subpoena
sec-investigation doj lawsuit sued litigation settlement-cost fine fined
penalty layoff layoffs lay-off workforce-reduction restructuring charge
impairment writedown write-down goodwill-impairment bankruptcy chapter-11
default defaulted covenant-breach delist delisting resign resigned
resignation departure cfo-departure ceo-departure auditor-change weak
weakness deteriorate deteriorated deteriorating decline declined declining
fall fell plunge plunged slump slowdown slowing contraction bearish
pessimism concern concerned risk risks uncertain uncertainty volatile
volatility shortfall short-fall recall recalled breach breached hack hacked
cyberattack outflow outflows downgrade-watch negative-watch junk
""".split())

assert len(POSITIVE_WORDS) >= 40 and len(NEGATIVE_WORDS) >= 40, \
    "lexicon must stay finance-specific and sizable"


def score_text(text: str) -> dict:
    """Score one document: (pos-neg)/(pos+neg+2) in (-1, 1).

    The +2 smoothing keeps single-word documents from pinning to +/-1.
    Returns {score, pos_hits, neg_hits, pos_words, neg_words}.
    """
    tokens = _WORD.findall((text or "").lower())
    pos = sorted({t for t in tokens if t in POSITIVE_WORDS})
    neg = sorted({t for t in tokens if t in NEGATIVE_WORDS})
    p, n = len(pos), len(neg)
    return {"score": (p - n) / (p + n + 2),
            "pos_hits": p, "neg_hits": n,
            "pos_words": pos, "neg_words": neg}


def sentiment_summary(cik: int, as_of, headlines: list | None = None,
                      filings_lookback_days: int = 180,
                      cache_dir: str = edgar.DEFAULT_CACHE_DIR) -> dict:
    """Aggregate polarity / momentum / velocity for ``cik`` at ``as_of``."""
    as_of = pd.Timestamp(as_of)
    docs = []
    for f in edgar.recent_filings(cik, as_of, forms=("8-K",),
                                  lookback_days=filings_lookback_days,
                                  cache_dir=cache_dir):
        s = score_text(f["description"])
        docs.append({"date": f["date"], "source": "8-K",
                     "text": f["description"], **s})
    if headlines:
        start = as_of - pd.Timedelta(days=90)
        for h in headlines:
            d = pd.Timestamp(h["date"])
            if d < start or d > as_of:
                continue
            s = score_text(h.get("text", ""))
            docs.append({"date": d.date().isoformat(), "source": "headline",
                         "text": h.get("text", ""), **s})
    docs.sort(key=lambda r: r["date"])
    n = len(docs)
    if n == 0:
        return {"polarity": None, "momentum_28d": None, "velocity_z": None,
                "n_docs": 0, "as_of": str(as_of.date()), "docs": []}

    scores = pd.Series([d["score"] for d in docs],
                       index=pd.to_datetime([d["date"] for d in docs]))
    polarity = float(scores.mean())

    cut = as_of - pd.Timedelta(days=28)
    cut2 = as_of - pd.Timedelta(days=56)
    recent = scores[scores.index > cut]
    prior = scores[(scores.index > cut2) & (scores.index <= cut)]
    momentum = (float(recent.mean() - prior.mean())
                if len(recent) and len(prior) else 0.0)

    # velocity: 7d doc count z-scored vs trailing 90d daily counts
    win_start = as_of - pd.Timedelta(days=90)
    daily = scores[scores.index > win_start].resample("D").size()
    daily = daily.reindex(pd.date_range(win_start + pd.Timedelta(days=1),
                                        as_of, freq="D"), fill_value=0)
    last7 = float(daily.tail(7).sum())
    mu, sd = float(daily.mean()), float(daily.std())
    velocity_z = (last7 - 7 * mu) / (sd * (7 ** 0.5)) if sd > 0 else 0.0

    return {"polarity": round(polarity, 4),
            "momentum_28d": round(momentum, 4),
            "velocity_z": round(velocity_z, 3),
            "n_docs": n,
            "as_of": str(as_of.date()),
            "docs": docs}
