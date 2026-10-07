"""GET /api/v1/etfs/{ticker}, /etfs/compare (stub, UNCONFIRMED demo).

NOTE: /etfs/compare is declared BEFORE /etfs/{ticker} so the literal path
is not shadowed by the path parameter.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app import data_stub
from app.routers import env, not_found
from app.schemas import EtfHolding, EtfProfile

router = APIRouter(tags=["etfs"])


def _profile_payload(p: dict) -> dict:
    return EtfProfile(
        ticker=p["ticker"], name=p["name"], issuer=p["issuer"],
        expense_ratio=p["expense_ratio"], aum=p["aum"],
        holdings=[EtfHolding(**h) for h in p["holdings"]],
        sector_exposure=p["sector_exposure"],
        concentration_top10=p["concentration_top10"],
        tracking_error=p["tracking_error"],
        data_status="UNCONFIRMED",
    ).model_dump()


@router.get("/etfs/compare")
def etf_compare(request: Request, tickers: str = Query(..., description="e.g. QQQ,SPY")):
    out = []
    for t in [x.strip().upper() for x in tickers.split(",") if x.strip()]:
        p = data_stub.etf_profile(t)
        if p is None:
            continue
        out.append(_profile_payload(p))
    return env(out, "UNCONFIRMED", request, as_of=data_stub.as_of_iso())


@router.get("/etfs/{ticker}")
def etf_profile(ticker: str, request: Request):
    p = data_stub.etf_profile(ticker)
    if p is None:
        not_found(f"ETF {ticker.upper()} not in stub universe")
    return env(_profile_payload(p), "UNCONFIRMED", request,
               as_of=data_stub.as_of_iso())
