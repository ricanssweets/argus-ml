"""GET /api/v1/assets* — asset reference + stub market data (UNCONFIRMED demo)."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app import data_stub
from app.routers import env, not_found
from app.schemas import (
    AssetDetail,
    AssetSummary,
    FundamentalsResponse,
    PriceBar,
    PricesResponse,
    TechnicalsResponse,
)

router = APIRouter(tags=["assets"])


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
    return env(data, "UNCONFIRMED", request)


@router.get("/assets/{ticker}")
def asset_detail(ticker: str, request: Request):
    a = data_stub.get_asset(ticker)
    if a is None:
        not_found(f"asset {ticker.upper()} not in stub universe")
    price, prev = a["price"], a["prev_close"]
    detail = AssetDetail(
        **{k: a.get(k) for k in AssetSummary.model_fields},
        price=price,
        prev_close=prev,
        change_pct=round((price - prev) / prev * 100, 2),
        market_cap=a.get("market_cap"),
        data_status="UNCONFIRMED",
    )
    return env(detail.model_dump(), "UNCONFIRMED", request, as_of=data_stub.as_of_iso())


@router.get("/assets/{ticker}/prices")
def asset_prices(
    ticker: str,
    request: Request,
    range: str = Query("1y", description="e.g. 1y (stub ignores, returns ~252 bars)"),
    interval: str = Query("1d"),
):
    if data_stub.get_asset(ticker) is None:
        not_found(f"asset {ticker.upper()} not in stub universe")
    bars = [PriceBar(**b).model_dump() for b in data_stub.synthetic_prices(ticker.upper())]
    payload = PricesResponse(
        ticker=ticker.upper(), interval=interval, bars=bars,
        data_status="UNCONFIRMED",
    )
    return env(payload.model_dump(), "UNCONFIRMED", request, as_of=data_stub.as_of_iso())


@router.get("/assets/{ticker}/fundamentals")
def asset_fundamentals(ticker: str, request: Request):
    if data_stub.get_asset(ticker) is None:
        not_found(f"asset {ticker.upper()} not in stub universe")
    f = data_stub.synthetic_fundamentals(ticker.upper())
    payload = FundamentalsResponse(
        ticker=f["ticker"], fiscal_period=f["fiscal_period"],
        reported_at=f["reported_at"], revenue=f["revenue"], eps=f["eps"],
        ebitda=f["ebitda"], gross_margin=f["gross_margin"],
        net_margin=f["net_margin"], roe=f["roe"], pe=f["pe"],
        forward_pe=f["forward_pe"], data_status="UNCONFIRMED",
    )
    return env(payload.model_dump(), "UNCONFIRMED", request, as_of=f["reported_at"])


@router.get("/assets/{ticker}/technicals")
def asset_technicals(ticker: str, request: Request):
    if data_stub.get_asset(ticker) is None:
        not_found(f"asset {ticker.upper()} not in stub universe")
    t = data_stub.synthetic_technicals(ticker.upper())
    payload = TechnicalsResponse(**{**t, "data_status": "UNCONFIRMED"})
    return env(payload.model_dump(), "UNCONFIRMED", request, as_of=t["as_of"])
