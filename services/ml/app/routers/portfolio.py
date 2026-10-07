"""POST /api/v1/portfolio/analyze — real risk/return analysis.

Real path (no stub): fetches REAL daily prices via app.prices (Yahoo
Finance, cached under ~/workspace/argus/data/raw/) and computes the
full risk payload with argus_quant.portfolio. No quant logic is
duplicated here.

Response shape follows API_SPEC.md: risk/return/vol/maxDD/beta/Sharpe/
Sortino/VaR/CVaR/correlation matrix/sector concentration/factor
exposure/diversification score/stress tests, wrapped in the
{data, meta{as_of, data_status, request_id}} envelope.

data_status=CONFIRMED (real prices). Unavailable fields are explicit
nulls with a reason, never 0. Fetch failures -> 503/422 HTTP errors,
never synthetic prices.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app import prices as prices_mod
from app.prices import PriceFetchError
from app.quant_compat import QUANT_AVAILABLE, quant_attr
from app.routers import env
from app.schemas import PortfolioAnalysis, PortfolioAnalyzeRequest

router = APIRouter(tags=["portfolio"])

DEFAULT_SCENARIOS = ["2008_crisis", "2020_crash", "2022_rate_shock",
                     "high_inflation", "rapid_rate_cuts", "recession",
                     "market_rally", "vol_spike"]


@router.post("/portfolio/analyze")
def analyze_portfolio(body: PortfolioAnalyzeRequest, request: Request):
    tickers = [p.ticker.upper() for p in body.positions]
    if not tickers:
        raise HTTPException(status_code=422,
                            detail="positions must not be empty")
    if len(set(tickers)) != len(tickers):
        raise HTTPException(status_code=422,
                            detail="duplicate tickers in positions")

    quant_portfolio = quant_attr("portfolio")
    if not QUANT_AVAILABLE or quant_portfolio is None:
        raise HTTPException(
            status_code=503,
            detail="argus_quant.portfolio is not available in this "
                   "deployment - cannot compute a real analysis")

    scenarios = body.scenarios or list(DEFAULT_SCENARIOS)
    need_vix = "vol_spike" in scenarios
    fetch_list = tickers + (["^VIX"] if need_vix else [])
    try:
        fetched = prices_mod.fetch_many(fetch_list)
    except PriceFetchError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    missing = [t for t in tickers if t not in fetched]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"no price data for: {', '.join(missing)}")

    prices = {t: fetched[t] for t in tickers}
    vix = (fetched["^VIX"]["close"] if need_vix and "^VIX" in fetched
           else None)

    positions = [{"ticker": p.ticker.upper(), "qty": p.qty}
                 for p in body.positions]
    try:
        result = quant_portfolio.analyze(
            prices, positions, scenarios=scenarios, vix=vix)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    conc = result["sector_concentration"]
    payload = PortfolioAnalysis(
        expected_return=result["expected_return"],
        volatility=result["volatility"],
        max_drawdown=result["max_drawdown"],
        beta=result["beta"],
        sharpe=result["sharpe"],
        sortino=result["sortino"],
        var_95=result["var_95"],
        cvar_95=result["cvar_95"],
        var_99=result["var_99"],
        cvar_99=result["cvar_99"],
        weights=result["weights"],
        correlation_matrix=result["correlation_matrix"],
        sector_concentration=conc["by_sector"],
        sector_hhi=conc["hhi"],
        diversification_score=result["diversification_score"],
        factor_exposure=result["factor_exposure"],
        vol_target_sizing=result["vol_target_sizing"],
        stress_tests=result["stress_tests"],
        data_status="CONFIRMED",
        notes=[
            "Real daily prices (Yahoo Finance, cached under "
            "data/raw/). Buy-and-hold portfolio; weights float with "
            "prices.",
            result["var_note"],
            "Sectors from a static ticker map; unknown tickers -> "
            "'Unknown'.",
            "Factor 'value' needs point-in-time fundamentals "
            "(unavailable from prices).",
            "Probabilistic estimate. Not investment advice.",
        ],
    )
    return env(payload.model_dump(), "CONFIRMED", request,
               as_of=result["as_of"])
