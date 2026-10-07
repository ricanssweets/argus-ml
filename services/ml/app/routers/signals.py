"""GET /api/v1/signals, /scanner, /market-regime (stub, UNCONFIRMED demo)."""

from __future__ import annotations

import hashlib
import os
import uuid

from fastapi import APIRouter, Query, Request

from app import data_stub
from app.routers import env
from app.schemas import MarketRegime, ScannerRow, Signal

router = APIRouter(tags=["signals"])

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_SIGNAL_TYPES = ["breakout", "momentum", "reversal", "rotation", "mean_reversion"]


def _strength(ticker: str, sig: str) -> float:
    h = hashlib.sha256(f"argus-stub:signal:{ticker}:{sig}".encode()).hexdigest()
    return round(0.45 + (int(h[:4], 16) % 5000) / 5000 * 0.5, 3)


@router.get("/signals")
def list_signals(
    request: Request,
    types: str = Query("", description="comma-separated signal types"),
    min_strength: float = Query(0.0, ge=0.0, le=1.0),
):
    wanted = [s.strip() for s in types.split(",") if s.strip()] or _SIGNAL_TYPES
    out = []
    for t in data_stub._ASSETS:
        for sig in wanted:
            if sig not in _SIGNAL_TYPES:
                continue
            st = _strength(t, sig)
            if st < min_strength:
                continue
            out.append(Signal(
                id=uuid.uuid4().hex[:12], as_of=data_stub.as_of_iso(),
                ticker=t, signal_type=sig, strength=st, horizon=20,
                setup_tags=[sig, "stub"], data_status="UNCONFIRMED",
            ).model_dump())
    out.sort(key=lambda r: r["strength"], reverse=True)
    return env(out, "UNCONFIRMED", request, as_of=data_stub.as_of_iso())


@router.get("/scanner")
def scanner(
    request: Request,
    setup: str = Query("momentum_breakout"),
    horizon: int = Query(20),
    limit: int = Query(25, ge=1, le=100),
):
    rows = []
    for t in data_stub._ASSETS:
        try:
            p = data_stub.synthetic_prediction(t, horizon)
        except KeyError:
            continue
        rows.append(ScannerRow(
            ticker=t, horizon=horizon, composite_score=p["composite_score"],
            p_positive=p["p_positive"], expected_return=p["expected_return"],
            risk_reward=p["risk_reward"],
            setup_tags=[setup, "stub"], data_status="UNCONFIRMED",
        ).model_dump())
    rows.sort(key=lambda r: r["composite_score"], reverse=True)
    return env(rows[:limit], "UNCONFIRMED", request, as_of=data_stub.as_of_iso())


@router.get("/market-regime")
def market_regime(request: Request):
    m = data_stub.market_regime_stub()
    payload = MarketRegime(
        regime=m["regime"], confidence=m["confidence"], as_of=m["as_of"],
        drivers=m["drivers"], history=m["history"], data_status="UNCONFIRMED",
    )
    return env(payload.model_dump(), "UNCONFIRMED", request, as_of=m["as_of"])


@router.get("/market-regime/history")
def market_regime_history(
    request: Request,
    days: int = Query(365, ge=30, le=2000),
):
    """Real regime history from data/regime_history.csv (rule-based classifier).

    Rows carry their own data_status (UNCONFIRMED where FRED credit/curve
    inputs were unreachable at build time -- see the CSV drivers column).
    """
    import csv
    import os

    csv_path = os.path.join(REPO_ROOT, "data", "regime_history.csv")
    rows = []
    try:
        with open(csv_path, newline="") as fh:
            for rec in csv.DictReader(fh):
                rows.append({
                    "time": rec["time"][:10],
                    "regime": rec["regime"],
                    "confidence": float(rec["confidence"]),
                    "data_status": rec.get("data_status", "UNCONFIRMED"),
                })
    except OSError:
        rows = []
    rows = rows[-days:]
    as_of = rows[-1]["time"] if rows else ""
    status = "LIKELY" if rows else "MISSING"
    return env({"history": rows, "count": len(rows), "as_of": as_of,
                "source": "argus_quant.regime (rule-based)",
                "disclaimer": ("Regime labels from a deterministic rule set, "
                               "not a prediction of future regimes.")},
               status, request, as_of=as_of)
