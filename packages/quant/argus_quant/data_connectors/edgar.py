"""SEC EDGAR connector (free, no API key).

Sources:
  - CIK map:        https://www.sec.gov/files/company_tickers.json
  - Company facts:  https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json
  - Submissions:    https://data.sec.gov/submissions/CIK{cik:010d}.json (8-K list)

SEC policy: every request MUST carry a descriptive User-Agent header;
requests without one are blocked (HTTP 403). We use
``ArgusResearch contact@example.com``. Politeness: >= 0.25 s between
requests; everything cached under ~/workspace/argus/data/edgar/.

Point-in-time discipline: each XBRL fact carries a ``filed`` date. The
``asof_*`` helpers return ONLY facts with ``filed <= as_of`` -- the filing
date is the knowledge date, never the fiscal period end. Facts with no
``filed`` date are excluded (cannot prove they were knowable).

Lookbacks:
  - Flow items (revenue, net income, OCF, capex, interest, D&A): trailing
    twelve months = sum of the latest 4 DISCRETE quarterly facts
    (duration 70-120 days; YTD contexts sharing the same (fy, fp) are
    excluded to avoid double-counting). Falls back to the latest annual
    fact when fewer than 4 quarters are knowable.
  - Balance-sheet items (equity, debt, cash) and shares outstanding:
    latest knowable point-in-time value (filed <= as_of).
Tag selection: when several candidate tags exist for one concept, the tag
with the most recently filed data wins (stale duplicates like AAPL's
legacy ``Revenues`` are never silently preferred).
"""

from __future__ import annotations

import json
import os
import time

import pandas as pd
import requests

USER_AGENT = "ArgusResearch contact@example.com"
_HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
_POLITE_SLEEP = 0.25

_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

DEFAULT_CACHE_DIR = os.path.expanduser("~/workspace/argus/data/edgar")

# Cache freshness (seconds). Facts are filtered point-in-time downstream,
# so a slightly stale cache cannot leak the future; freshness only bounds
# how old the *newest knowable* filing may be.
_TTL_TICKERS = 30 * 86400
_TTL_FACTS = 7 * 86400
_TTL_SUBMISSIONS = 1 * 86400


def _get_json(url: str, cache_path: str, ttl: int) -> dict:
    """GET with disk cache; polite sleep before any network hit."""
    if os.path.exists(cache_path) and time.time() - os.path.getmtime(cache_path) < ttl:
        with open(cache_path, encoding="utf-8") as fh:
            return json.load(fh)
    time.sleep(_POLITE_SLEEP)
    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return data


# ---------------------------------------------------------------------------
# CIK map
# ---------------------------------------------------------------------------
def load_ticker_cik_map(cache_dir: str = DEFAULT_CACHE_DIR) -> dict:
    """Return {TICKER: cik_int} from the SEC company_tickers.json (cached)."""
    data = _get_json(_TICKER_URL, os.path.join(cache_dir, "company_tickers.json"),
                     _TTL_TICKERS)
    return {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}


def cik_for_ticker(ticker: str, cache_dir: str = DEFAULT_CACHE_DIR):
    """CIK for a ticker, or None when the ticker is not in the SEC map."""
    return load_ticker_cik_map(cache_dir).get(ticker.upper())


# ---------------------------------------------------------------------------
# Company facts
# ---------------------------------------------------------------------------
def get_company_facts(cik: int, cache_dir: str = DEFAULT_CACHE_DIR) -> dict:
    """Raw companyfacts JSON for a CIK (cached 7d)."""
    return _get_json(_FACTS_URL.format(cik=cik),
                     os.path.join(cache_dir, "facts", "CIK%010d.json" % cik),
                     _TTL_FACTS)


def _find_fact(raw: dict, tags: list):
    """First (namespace, tag, fact) found; searches us-gaap then dei."""
    facts = raw.get("facts", {})
    for ns in ("us-gaap", "dei"):
        for tag in tags:
            fact = facts.get(ns, {}).get(tag)
            if fact:
                return ns, tag, fact
    return None


def _select_tag(raw: dict, tags: list, as_of: pd.Timestamp):
    """Pick the winning tag: the candidate with the most recently filed entry.

    Filers often keep stale duplicate tags (e.g. AAPL's legacy ``Revenues``
    whose latest filing is 2018 while ``RevenueFromContractWithCustomer...``
    is current). First-present wins would silently use ancient data, so we
    score each candidate by max(filed) over entries with filed <= as_of.
    Returns (namespace, tag, fact) or None.
    """
    facts = raw.get("facts", {})
    best, best_filed = None, None
    for ns in ("us-gaap", "dei"):
        for tag in tags:
            fact = facts.get(ns, {}).get(tag)
            if not fact:
                continue
            latest = None
            for entries in fact.get("units", {}).values():
                for e in entries:
                    if not e.get("filed"):
                        continue
                    fl = pd.Timestamp(e["filed"])
                    if fl <= as_of and (latest is None or fl > latest):
                        latest = fl
            if latest is not None and (best_filed is None or latest > best_filed):
                best, best_filed = (ns, tag, fact), latest
    return best


def fact_entries(raw: dict, tags: list, as_of) -> pd.DataFrame:
    """All knowable entries for the best-matching tag: rows with ``filed <= as_of``.

    Returns a DataFrame with columns
    [filed, start, end, duration_days, form, fy, fp, frame, val, accn, unit,
    tag], sorted by (filed desc, end desc). Entries without a ``filed``
    date are excluded. ``duration_days`` = (end - start) lets downstream
    code distinguish discrete-quarter contexts (~90d) from YTD contexts
    (~180/270d) that share the same (fy, fp).
    """
    as_of = pd.Timestamp(as_of)
    found = _select_tag(raw, tags, as_of)
    cols = ["filed", "start", "end", "duration_days", "form", "fy", "fp",
            "frame", "val", "accn", "unit", "tag"]
    if found is None:
        return pd.DataFrame(columns=cols)
    _, tag, fact = found
    rows = []
    for unit, entries in fact.get("units", {}).items():
        for e in entries:
            if not e.get("filed"):
                continue  # cannot prove knowability -> exclude
            filed = pd.Timestamp(e["filed"])
            if filed > as_of:
                continue
            start = pd.Timestamp(e["start"]) if e.get("start") else pd.NaT
            end = pd.Timestamp(e.get("end")) if e.get("end") else pd.NaT
            dur = (end - start).days if pd.notna(start) and pd.notna(end) else None
            rows.append({
                "filed": filed, "start": start, "end": end,
                "duration_days": dur,
                "form": e.get("form"), "fy": e.get("fy"), "fp": e.get("fp"),
                "frame": e.get("frame"), "val": e.get("val"),
                "accn": e.get("accn"), "unit": unit, "tag": tag,
            })
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df = df.sort_values(["filed", "end"], ascending=[False, False])
    return df


def _sum(*vs):
    total, any_v = 0.0, False
    for v in vs:
        if v is None:
            continue
        total += v
        any_v = True
    return total if any_v else None


# Fact-tag groups. Each group lists tags in preference order; the first
# tag present in the company's facts wins.
_TAG_GROUPS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss"],
    "gross_profit": ["GrossProfit"],
    "op_income": ["OperatingIncomeLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "interest": ["InterestExpense"],
    "dna": ["DepreciationDepletionAndAmortization"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "debt_lt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "debt_cur": ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "shares": ["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"],
}

_FLOW_GROUPS = {"revenue", "net_income", "gross_profit", "op_income", "ocf",
                "capex", "interest", "dna"}


def asof_facts(cik: int, as_of, tags=None,
               cache_dir: str = DEFAULT_CACHE_DIR) -> dict:
    """{tag: entries DataFrame} for requested tags, only filed <= as_of."""
    raw = get_company_facts(cik, cache_dir)
    wanted = tags if tags is not None else [t for group in _TAG_GROUPS.values()
                                            for t in group]
    return {tag: fact_entries(raw, [tag], as_of) for tag in wanted}


def _latest_annual(df: pd.DataFrame):
    """Latest annual entry (fp FY*, prefer 10-K forms), or None."""
    if df.empty:
        return None
    ann = df[df["fp"].astype(str).str.startswith("FY")]
    if ann.empty:
        return None
    k = ann[ann["form"].isin(["10-K", "10-K/A"])]
    return (k.iloc[0] if not k.empty else ann.iloc[0])


def _ttm_value(df: pd.DataFrame):
    """Trailing-twelve-months for a quarterly flow item.

    XBRL 10-Qs carry BOTH discrete-quarter and year-to-date contexts under
    the same (fy, fp); summing both would double-count. We keep only
    discrete-quarter contexts (duration 70-120 days), dedupe by (fy, fp)
    keeping the latest filed, and sum the latest 4 by period end. Falls
    back to the latest annual value when fewer than 4 discrete quarters
    are knowable. Returns (value, method).
    """
    if df.empty:
        return None, "missing"
    q = df[df["fp"].astype(str).str.startswith("Q")].copy()
    q = q[q["duration_days"].between(70, 120)]
    if not q.empty:
        q = q.drop_duplicates(subset=["fy", "fp"], keep="first")
        q = q.sort_values("end", ascending=False).head(4)
        if len(q) == 4 and q["val"].notna().all():
            return float(q["val"].sum()), "ttm_4q"
    ann = _latest_annual(df)
    if ann is not None and pd.notna(ann["val"]):
        return float(ann["val"]), "annual"
    return None, "missing"


def _point_value(df: pd.DataFrame):
    """Latest knowable point value + its filed date."""
    if df.empty or pd.isna(df.iloc[0]["val"]):
        return None, None
    return float(df.iloc[0]["val"]), df.iloc[0]["filed"]


def _max_filed(cur, df: pd.DataFrame, method: str):
    if df.empty:
        return cur
    if method == "ttm_4q":
        q = df[df["fp"].astype(str).str.startswith("Q")]
        q = q.drop_duplicates(subset=["fy", "fp"], keep="first")
        q = q.sort_values("end", ascending=False).head(4)
        newest = q["filed"].max()
    else:
        ann = _latest_annual(df)
        newest = ann["filed"] if ann is not None else df["filed"].max()
    if pd.isna(newest):
        return cur
    return newest if (cur is None or newest > cur) else cur


def compute_fundamentals(cik: int, as_of, price: float,
                         cache_dir: str = DEFAULT_CACHE_DIR) -> dict:
    """Point-in-time fundamental ratios for ``cik`` at ``as_of``.

    ``price`` is the latest close <= as_of (caller's responsibility; the
    smoke script uses yfinance closes). Every ratio documents its formula
    below. Fields that cannot be built from knowable facts are None;
    ``reported_at`` is the newest filing date actually used.
    """
    as_of = pd.Timestamp(as_of)
    raw = get_company_facts(cik, cache_dir)
    vals: dict = {}
    methods: dict = {}
    newest_filed = None

    for group, tags in _TAG_GROUPS.items():
        df = fact_entries(raw, tags, as_of)
        if group in _FLOW_GROUPS:
            v, m = _ttm_value(df)
            vals[group] = v
            methods[group] = m
            newest_filed = _max_filed(newest_filed, df, m)
        else:
            v, filed = _point_value(df)
            vals[group] = v
            methods[group] = "point" if v is not None else "missing"
            if filed is not None and (newest_filed is None or filed > newest_filed):
                newest_filed = filed

    rev, ni = vals["revenue"], vals["net_income"]
    equity, cash = vals["equity"], vals["cash"]
    debt = _sum(vals["debt_lt"], vals["debt_cur"])
    shares = vals["shares"]
    mcap = price * shares if (price and shares) else None
    ev = (mcap + debt - cash) if (mcap is not None and debt is not None
                                 and cash is not None) else None
    ebitda = _sum(vals["op_income"], vals["dna"])  # EBIT + D&A
    fcf = (vals["ocf"] - vals["capex"]
           if vals["ocf"] is not None and vals["capex"] is not None else None)

    def ratio(num, den):
        return num / den if (num is not None and den not in (None, 0)) else None

    out = {
        "cik": cik,
        "as_of": str(as_of.date()),
        "reported_at": str(newest_filed.date()) if newest_filed is not None else None,
        "price": float(price),
        "market_cap": mcap,
        "enterprise_value": ev,
        # Valuation: P/E and EV/EBITDA use TTM earnings; negative earnings -> None
        "pe": ratio(mcap, ni) if (ni or 0) > 0 else None,
        "ps": ratio(mcap, rev),
        "pb": ratio(mcap, equity),
        "ev_ebitda": ratio(ev, ebitda) if (ebitda or 0) > 0 else None,
        "ev_ebitda_basis": "ebitda" if vals["dna"] else "ebit",
        "fcf_yield": ratio(fcf, mcap),
        # Profitability
        "gross_margin": ratio(vals["gross_profit"], rev),
        "net_margin": ratio(ni, rev),
        "roe": ratio(ni, equity),
        # ROIC = operating income / invested capital (pre-tax EBIT proxy,
        # documented simplification; invested capital = equity + debt - cash)
        "roic": ratio(vals["op_income"],
                      _sum(equity, debt, -cash if cash else None)),
        # Leverage / coverage
        "debt_to_equity": ratio(debt, equity),
        "interest_coverage": ratio(vals["op_income"], vals["interest"]),
        "ttm_revenue": rev,
        "ttm_net_income": ni,
        "ttm_fcf": fcf,
        "ttm_methods": {g: methods[g] for g in _FLOW_GROUPS},
    }
    return out


def fundamentals_for_ticker(ticker: str, as_of, price: float,
                            cache_dir: str = DEFAULT_CACHE_DIR):
    """Convenience wrapper: ticker -> CIK -> :func:`compute_fundamentals`."""
    cik = cik_for_ticker(ticker, cache_dir)
    if cik is None:
        return None
    return compute_fundamentals(cik, as_of, price, cache_dir)


# ---------------------------------------------------------------------------
# Submissions (8-K list for the sentiment engine)
# ---------------------------------------------------------------------------
def get_submissions(cik: int, cache_dir: str = DEFAULT_CACHE_DIR) -> dict:
    """Raw submissions JSON for a CIK (cached 1d)."""
    return _get_json(_SUBMISSIONS_URL.format(cik=cik),
                     os.path.join(cache_dir, "submissions",
                                  "CIK%010d.json" % cik),
                     _TTL_SUBMISSIONS)


def recent_filings(cik: int, as_of, forms=("8-K",), lookback_days: int = 365,
                   cache_dir: str = DEFAULT_CACHE_DIR) -> list:
    """Filings of the given forms with filingDate in [as_of-lookback, as_of].

    Returns [{date, form, description, accession}]; sorted oldest -> newest.
    Only the ``filings.recent`` block is used (documented limitation:
    ~1000 most recent filings; plenty for a 1y sentiment window).
    """
    as_of = pd.Timestamp(as_of)
    start = as_of - pd.Timedelta(days=lookback_days)
    raw = get_submissions(cik, cache_dir)
    recent = raw.get("filings", {}).get("recent", {})
    out = []
    n = len(recent.get("filingDate", []))
    forms_l = list(forms)
    for i in range(n):
        form = (recent.get("form", [""] * n))[i]
        if form not in forms_l:
            continue
        d = pd.Timestamp(recent["filingDate"][i])
        if d < start or d > as_of:
            continue
        desc = (recent.get("primaryDocumentDescription", [""] * n))[i] or ""
        items = (recent.get("items", [""] * n))[i] or ""
        out.append({"date": d.date().isoformat(), "form": form,
                    "description": ("%s %s" % (desc, items)).strip(),
                    "accession": (recent.get("accessionNumber", [""] * n))[i]})
    out.sort(key=lambda r: r["date"])
    return out
