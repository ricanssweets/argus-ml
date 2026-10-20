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

_WORD = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")

# --- Multi-word phrase normalisation --------------------------------------
# The lexicon is keyed by single hyphenated tokens (``chapter-11``,
# ``all-time-high``, ``write-down`` ...). Filers and headlines write those
# concepts as separate words ("chapter 11", "all time high", "write down"),
# so the raw tokenizer could never emit the lexicon key and the phrase
# scored 0.0 (neutral) -- a bankruptcy filing read as "no news". Each
# pattern below rewrites the spaced/inflected surface form to the canonical
# lexicon token BEFORE tokenisation. Order matters only for overlaps; every
# pattern is anchored on word boundaries.
_PHRASES = [
    (re.compile(r"\bchapter\s+(?:11|7|13|15)\b"), "chapter-11"),
    (re.compile(r"\ball[\s-]time[\s-]highs?\b"), "all-time-high"),
    (re.compile(r"\brecord[\s-]highs?\b"), "record-high"),
    (re.compile(r"\bwrite[\s-]?downs?\b"), "write-down"),
    (re.compile(r"\bwrit(?:e|ing|ten)[\s-]?offs?\b"), "write-down"),
    (re.compile(r"\blay[\s-]?offs?\b"), "lay-off"),
    (re.compile(r"\blaying\s+off\b|\blaid\s+off\b"), "lay-off"),
    (re.compile(r"\bwork[\s-]?force\s+reductions?\b"), "workforce-reduction"),
    (re.compile(r"\bprofit[\s-]warnings?\b"), "profit-warning"),
    (re.compile(r"\bguidance[\s-]cuts?\b"), "guidance-cut"),
    (re.compile(r"\bmargin[\s-]expansions?\b"), "margin-expansion"),
    (re.compile(r"\bauditor[\s-]change\b"), "auditor-change"),
    (re.compile(r"\bsec[\s-]investigations?\b"), "sec-investigation"),
    (re.compile(r"\bsettlement[\s-]costs?\b"), "settlement-cost"),
    (re.compile(r"\bsettlement[\s-]wins?\b"), "settlement-win"),
    (re.compile(r"\bnegative[\s-]watch\b"), "negative-watch"),
    (re.compile(r"\bdowngrade[\s-]watch\b"), "downgrade-watch"),
    (re.compile(r"\bcovenant[\s-]breachs?\b|\bcovenant[\s-]breaches\b"),
     "covenant-breach"),
    (re.compile(r"\bfda[\s-]approvals?\b"), "fda-approval"),
    (re.compile(r"\braise[sd]?\s+(?:its\s+)?guidance\b"), "raised-guidance"),
    (re.compile(r"\braise[sd]?\s+(?:its\s+)?dividends?\b"), "raised-dividend"),
    (re.compile(r"\bshort[\s-]?falls?\b"), "short-fall"),
    (re.compile(r"\bgoodwill[\s-]impairments?\b"), "goodwill-impairment"),
    (re.compile(r"\bcontract[\s-]award(?:ed)?\b"), "contract-awarded"),
    (re.compile(r"\b(?:cfo|chief\s+financial\s+officer)[\s-]departures?\b"),
     "cfo-departure"),
    (re.compile(r"\b(?:ceo|chief\s+executive\s+officer)[\s-]departures?\b"),
     "ceo-departure"),
]

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

# Reachability invariant: every lexicon entry (including the 25 hyphenated
# ones) must be emittable by the tokenizer, otherwise it is dead weight
# that appears to widen coverage without ever matching.
_LEXICON = POSITIVE_WORDS | NEGATIVE_WORDS
_UNREACHABLE = sorted(w for w in _LEXICON if not _WORD.fullmatch(w))
assert not _UNREACHABLE, \
    f"lexicon entries the tokenizer can never emit: {_UNREACHABLE}"


def _tokens(text: str) -> set[str]:
    """Candidate lexicon keys for ``text``.

    Normalises multi-word surface forms to their canonical hyphenated
    lexicon token, tokenises (hyphens/apostrophes/digits kept inside a
    token, so ``chapter-11`` stays one token), and ALSO contributes the
    hyphen-separated parts of every compound token. Keeping the parts
    matters for recall: ``cost-cutting`` must still match ``cutting``.
    """
    low = (text or "").lower()
    for pattern, canonical in _PHRASES:
        low = pattern.sub(canonical, low)
    out: set[str] = set()
    for tok in _WORD.findall(low):
        out.add(tok)
        if "-" in tok:
            out.update(p for p in tok.split("-") if p)
    return out


def score_text(text: str) -> dict:
    """Score one document: (pos-neg)/(pos+neg+2) in (-1, 1).

    The +2 smoothing keeps single-word documents from pinning to +/-1.
    Returns {score, pos_hits, neg_hits, pos_words, neg_words, no_hits}.
    ``no_hits`` is True when the document matched no lexicon entry: the
    score is then 0.0 by the documented smoothing convention (a neutral
    reading, not a fabricated one) and callers can tell the two apart.
    """
    toks = _tokens(text)
    pos = sorted(t for t in toks if t in POSITIVE_WORDS)
    neg = sorted(t for t in toks if t in NEGATIVE_WORDS)
    p, n = len(pos), len(neg)
    return {"score": (p - n) / (p + n + 2),
            "pos_hits": p, "neg_hits": n,
            "pos_words": pos, "neg_words": neg,
            "no_hits": (p + n) == 0}


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
