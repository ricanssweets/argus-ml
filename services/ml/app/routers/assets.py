"""GET /api/v1/assets* — asset reference + REAL market data.

The asset reference rows (ticker/name/sector/industry) come from the stub
universe and are labelled UNCONFIRMED — they are reference metadata, not
measured market data.

Market data does NOT come from the stub synthesizers any more:

  * ``GET /assets/{ticker}/prices``      -> app.prices.fetch_yahoo_daily
    (real Yahoo daily bars, baked-snapshot fallback). A price fetch failure
    returns an explicit HTTP error envelope; synthetic bars are never served
    (app/prices.py: "Prices are NEVER synthesized here").
  * ``GET /assets/{ticker}``             -> price / prev_close / change_pct
    from those real bars; ``market_cap`` is null (no real cap source is
    wired here — the stub's number is not served).
  * ``GET /assets/{ticker}/technicals``  -> indicators computed from the real
    bars (formulas in app.observatory.technicals)
  * ``GET /assets/{ticker}/fundamentals``-> EDGAR SEC company facts, or
    data_status=MISSING with null fields.

Missing data is REPORTED (data_status), never fabricated.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from app import data_stub, observatory
from app.routers import env, not_found
from app.schemas import (
    AssetDetail,
    AssetSummary,
    FundamentalsResponse,
    PriceBar,
    PricesResponse,
    TechnicalsResponse,
)

log = logging.getLogger("argus.ml.assets")
router = APIRouter(tags=["assets"])

# Daily bars per requested range (the only interval app.prices provides).
_RANGE_BARS = {
    "1d": 1, "5d": 5, "1mo": 21, "3mo": 63, "6mo": 126,
    "1y": 252, "2y": 504, "5y": 1260, "10y": 2520, "max": None,
}


def _status_for(fresh: bool) -> str:
    """Fresh real bars -> LIKELY; stale snapshot -> UNCONFIRMED."""
    return "LIKELY" if fresh else "UNCONFIRMED"


@router.get("/assets")
def list_assets(
    request: Request,
    query: str = Query("", description="ticker/name search"),
    type: str = Query("", description="STOCK|ETF|INDEX|CRYPTO|FUTURE"),
    sector: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
):
    assets = data_stub.list_assets(query, type, sector, limit)
    data = [AssetSummary(**{k: a.get(k) for k in AssetSummary.model_fields}).model_dump()
            for a in assets]
    # Reference metadata rows really exist (stub universe) -> UNCONFIRMED,
    # not MISSING. No measured market data is included in this payload.
    return env(data, "UNCONFIRMED", request)


@router.get("/assets/{ticker}")
def asset_detail(ticker: str, request: Request):
    t = ticker.upper()
    a = data_stub.get_asset(t)
    if a is None:
        not_found(f"asset {t} not in stub universe")

    summary = None
    try:
        summary = observatory.price_summary(t)
    except Exception as exc:  # noqa: BLE001 - no prices -> null, never invented
        log.warning("real price unavailable for %s: %s: %s",
                    t, type(exc).__name__, exc)

    if summary is None:
        price = prev = change_pct = None
        status, as_of = "MISSING", data_stub.utcnow_iso()
    else:
        price, prev = summary["price"], summary["prev_close"]
        # UNIT CONTRACT (apps/web/lib/types.ts:62-66 and lib/format.ts:36-45):
        # change_pct is ALREADY a percent (1.23 == +1.23%); the web formats it
        # with retPct1(), not with the fraction-based ret1().
        change_pct = round((price - prev) / prev * 100, 4) if prev else None
        status, as_of = _status_for(summary["fresh"]), summary["as_of"]

    detail = AssetDetail(
        **{k: a.get(k) for k in AssetSummary.model_fields},
        price=price,
        prev_close=prev,
        change_pct=change_pct,
        # The stub universe carries an invented market cap; no real cap source
        # is wired into this endpoint, so it is reported as null (MISSING)
        # rather than served as product output.
        market_cap=None,
        data_status=status,
    )
    return env(detail.model_dump(), status, request, as_of=as_of)


@router.get("/assets/{ticker}/prices")
def asset_prices(
    ticker: str,
    request: Request,
    range: str = Query("1y", description="1d|5d|1mo|3mo|6mo|1y|2y|5y|10y|max"),
    interval: str = Query("1d", description="daily bars only"),
):
    t = ticker.upper()
    if data_stub.get_asset(t) is None:
        not_found(f"asset {t} not in stub universe")
    if interval != "1d":
        raise HTTPException(
            status_code=400,
            detail=(f"interval '{interval}' is not available: app.prices "
                    f"provides real daily bars only and no bar is synthesized"))
    if range not in _RANGE_BARS:
        raise HTTPException(
            status_code=400,
            detail=(f"range '{range}' is not supported; use one of "
                    f"{sorted(_RANGE_BARS)}"))

    try:
        df = observatory.daily_bars(t)
    except Exception as exc:  # PriceFetchError or transport failure
        raise HTTPException(
            status_code=502,
            detail=(f"real price data unavailable for {t} "
                    f"({type(exc).__name__}: {exc}); no synthetic bars are "
                    f"served")) from exc

    n = _RANGE_BARS[range]
    window = df.tail(n) if n else df
    bars = []
    for ts, row in window.iterrows():
        o = row.get("open")
        h = row.get("high")
        lo = row.get("low")
        c = row.get("close")
        if any(v is None or v != v for v in (o, h, lo, c)):  # NaN -> drop
            continue
        vol = row.get("volume")
        bars.append(PriceBar(
            time=ts.strftime("%Y-%m-%d"),
            open=float(o), high=float(h), low=float(lo), close=float(c),
            volume=(int(vol) if vol is not None and vol == vol else None),
        ).model_dump())

    if not bars:
        payload = PricesResponse(ticker=t, interval="1d", bars=[],
                                 data_status="MISSING")
        return env(payload.model_dump(), "MISSING", request)

    as_of = bars[-1]["time"]
    status = _status_for(observatory.freshness(
        window.index[-1].date() if hasattr(window.index[-1], "date")
        else window.index[-1]))
    payload = PricesResponse(ticker=t, interval="1d", bars=bars,
                             data_status=status)
    return env(payload.model_dump(), status, request, as_of=as_of)


@router.get("/assets/{ticker}/fundamentals")
def asset_fundamentals(ticker: str, request: Request):
    t = ticker.upper()
    if data_stub.get_asset(t) is None:
        not_found(f"asset {t} not in stub universe")

    price = None
    try:
        summary = observatory.price_summary(t)
        price = summary["price"] if summary else None
    except Exception:  # noqa: BLE001
        price = None

    f = observatory.fundamentals(t, price) if price is not None else None
    if f is None:
        # No SEC facts reachable / no real source: report MISSING, do not
        # fall back to data_stub.synthetic_fundamentals().
        payload = FundamentalsResponse(ticker=t, data_status="MISSING")
        return env(payload.model_dump(), "MISSING", request)

    payload = FundamentalsResponse(
        ticker=t, fiscal_period=f["fiscal_period"],
        reported_at=f["reported_at"], revenue=f["revenue"], eps=f["eps"],
        ebitda=f["ebitda"], gross_margin=f["gross_margin"],
        net_margin=f["net_margin"], roe=f["roe"], pe=f["pe"],
        forward_pe=f["forward_pe"], data_status="LIKELY",
    )
    return env(payload.model_dump(), "LIKELY", request,
               as_of=f["reported_at"] or data_stub.utcnow_iso())


@router.get("/assets/{ticker}/technicals")
def asset_technicals(ticker: str, request: Request):
    t = ticker.upper()
    if data_stub.get_asset(t) is None:
        not_found(f"asset {t} not in stub universe")

    try:
        tech = observatory.technicals(t)
    except Exception as exc:  # price fetch failure -> MISSING, never synthetic
        log.warning("technicals unavailable for %s: %s: %s",
                    t, type(exc).__name__, exc)
        tech = None

    if tech is None:
        payload = TechnicalsResponse(ticker=t, as_of="", data_status="MISSING")
        return env(payload.model_dump(), "MISSING", request)

    status = _status_for(tech["fresh"])
    payload = TechnicalsResponse(
        ticker=t, as_of=tech["as_of"], rsi_14=tech["rsi_14"],
        macd=tech["macd"], macd_signal=tech["macd_signal"],
        sma_50=tech["sma_50"], sma_200=tech["sma_200"],
        bollinger_upper=tech["bollinger_upper"],
        bollinger_lower=tech["bollinger_lower"], atr_14=tech["atr_14"],
        data_status=status,
    )
    return env(payload.model_dump(), status, request, as_of=tech["as_of"])
