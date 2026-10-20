"""GET /api/v1/signals, /scanner, /market-regime — real data only.

What changed and why (the previous version fabricated all three):

  * ``/signals``      used ``sha256("argus-stub:signal:<ticker>:<type>")`` to
                      turn a hash into a "strength" in [0.45, 0.95]. That
                      number measured nothing. Strengths now come from real
                      indicators on real daily bars (breakout = Donchian
                      position, momentum = risk-adjusted 63d return, ...) —
                      see app.observatory.signal_strengths for the formulas.
  * ``/scanner``      ranked ``data_stub.synthetic_prediction`` numbers.
                      Rows now come from the live pipeline
                      (app.predict.build_live_prediction) and only tickers the
                      pipeline can serve on real data are returned; if it can
                      serve none, the response is MISSING with no rows.
  * ``/market-regime``served ``data_stub.market_regime_stub()``. It now uses
                      argus_quant.regime.classify_regime on real SPY/^VIX
                      bars (same code path as app.predict), and reports
                      MISSING when those bars are unavailable.

Missing data is REPORTED (data_status), never fabricated.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Query, Request
from pydantic import Field

from app import data_stub, observatory
from app.routers import env
from app.schemas import MarketRegime, ScannerRow, Signal

log = logging.getLogger("argus.ml.signals")
router = APIRouter(tags=["signals"])

_SIGNAL_TYPES = ["breakout", "momentum", "reversal", "rotation", "mean_reversion"]


class SignalOut(Signal):
    """Additive provenance for a price-derived signal (real inputs shown)."""
    source: str = "MISSING"
    inputs: Dict[str, Any] = Field(default_factory=dict)


class ScannerRowOut(ScannerRow):
    as_of: str = ""
    pipeline: str = "live"
    source: str = "app.predict.build_live_prediction"


def _status_for(fresh: bool) -> str:
    return "LIKELY" if fresh else "UNCONFIRMED"


def _worst(statuses: List[str]) -> str:
    worst = max((observatory.STATUS_RANK.get(s, 4) for s in statuses),
                default=4)
    for name, rank in observatory.STATUS_RANK.items():
        if rank == worst:
            return name
    return "MISSING"


def _signal_id(ticker: str, sig: str, as_of: str) -> str:
    """Stable content-derived id (the hash is an identifier, not a metric)."""
    return hashlib.sha256(f"{ticker}:{sig}:{as_of}".encode()).hexdigest()[:12]


@router.get("/signals")
def list_signals(
    request: Request,
    types: str = Query("", description="comma-separated signal types"),
    min_strength: float = Query(0.0, ge=0.0, le=1.0),
):
    wanted = [s.strip() for s in types.split(",") if s.strip()] or _SIGNAL_TYPES
    wanted = [s for s in wanted if s in _SIGNAL_TYPES]
    spy_close = None
    try:
        spy_close = observatory.daily_bars("SPY")["close"].dropna().to_numpy()
    except Exception as exc:  # noqa: BLE001 - rotation needs SPY; others do not
        log.warning("SPY bars unavailable for rotation: %s",
                    type(exc).__name__)

    out: List[Dict[str, Any]] = []
    statuses: List[str] = []
    for t in data_stub._ASSETS:
        try:
            sig_data = observatory.signal_strengths(t, spy_close=spy_close)
        except Exception as exc:  # noqa: BLE001 - skip, never invent the ticker
            log.warning("signals unavailable for %s: %s: %s",
                        t, type(exc).__name__, exc)
            continue
        if sig_data is None:
            continue
        status = _status_for(sig_data["fresh"])
        for sig in wanted:
            st = sig_data["strengths"].get(sig)
            if st is None or st < min_strength:
                continue
            out.append(SignalOut(
                id=_signal_id(t, sig, sig_data["as_of"]),
                as_of=sig_data["as_of"], ticker=t, signal_type=sig,
                strength=st, horizon=20,
                setup_tags=[sig, "price_derived"],
                data_status=status, source=sig_data["source"],
                inputs=sig_data["inputs"],
            ).model_dump())
        statuses.append(status)
    if not out:
        if not statuses:
            # No ticker had usable real bars: explicitly MISSING, no rows.
            return env([], "MISSING", request, as_of=data_stub.utcnow_iso())
        # Data existed; the min_strength filter removed every row.
        return env([], _worst(statuses), request, as_of=data_stub.utcnow_iso())
    out.sort(key=lambda r: r["strength"], reverse=True)
    return env(out, _worst(statuses), request, as_of=out[0]["as_of"])


@router.get("/scanner")
def scanner(
    request: Request,
    setup: str = Query("momentum_breakout"),
    horizon: int = Query(20),
    limit: int = Query(25, ge=1, le=100),
):
    """Ranked rows from the live pipeline; unservable tickers are skipped."""
    from app import predict as live

    rows: List[Dict[str, Any]] = []
    statuses: List[str] = []
    failures: List[str] = []
    for t in data_stub._ASSETS:
        try:
            p = live.build_live_prediction(t, horizon)
        except Exception as exc:  # noqa: BLE001 - report, never synthesize
            failures.append(f"{t}: {type(exc).__name__}")
            log.warning("scanner: live pipeline failed for %s h=%s: %s",
                        t, horizon, type(exc).__name__)
            continue
        rows.append(ScannerRowOut(
            ticker=t, horizon=horizon, composite_score=p["composite_score"],
            p_positive=p["p_positive"], expected_return=p["expected_return"],
            risk_reward=p["risk_reward"],
            setup_tags=[setup, "live_pipeline"], data_status=p["data_status"],
            as_of=p["as_of"],
        ).model_dump())
        statuses.append(p["data_status"])
    rows.sort(key=lambda r: r["composite_score"], reverse=True)
    if not rows:
        # No ticker could be served on real data: an empty, explicitly
        # MISSING response instead of the old synthetic rankings.
        resp = env([], "MISSING", request)
        resp["meta"]["notes"] = (
            [f"live pipeline produced no row for horizon {horizon}"]
            + failures)
        return resp
    return env(rows[:limit], _worst(statuses), request,
               as_of=rows[0]["as_of"])


@router.get("/market-regime")
def market_regime(request: Request):
    try:
        reg = observatory.market_regime()
    except Exception as exc:  # noqa: BLE001 - report, never stub the regime
        log.warning("market regime unavailable: %s: %s",
                    type(exc).__name__, exc)
        reg = None
    if reg is None:
        payload = MarketRegime(
            regime="MISSING", confidence=0.0, as_of="", drivers=[
                "no real SPY/^VIX regime inputs available: no classification "
                "is made (the stub regime is not served)"],
            history=[], data_status="MISSING",
        )
        return env(payload.model_dump(), "MISSING", request)
    payload = MarketRegime(
        regime=reg["regime"],
        confidence=float(min(max(reg["confidence"], 0.0), 1.0)),
        as_of=reg["as_of"], drivers=reg["drivers"], history=[],
        data_status=reg["data_status"],
    )
    return env(payload.model_dump(), reg["data_status"], request,
               as_of=reg["as_of"])


@router.get("/market-regime/history")
def market_regime_history(
    request: Request,
    days: int = Query(365, ge=30, le=2000),
):
    """Real regime history from data/regime_history.csv (rule-based classifier).

    Rows carry their own data_status (UNCONFIRMED where FRED credit/curve
    inputs were unreachable at build time -- see the CSV drivers column).
    The CSV is absent from a clean checkout: that is reported as MISSING with
    count=0, not filled with a synthetic series.
    """
    rows = observatory.regime_history_csv(days)
    as_of = rows[-1]["time"] if rows else ""
    status = "LIKELY" if rows else "MISSING"
    return env({"history": rows, "count": len(rows), "as_of": as_of,
                "source": "argus_quant.regime (rule-based)",
                "disclaimer": ("Regime labels from a deterministic rule set, "
                               "not a prediction of future regimes.")},
               status, request, as_of=as_of or data_stub.utcnow_iso())
