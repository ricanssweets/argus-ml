"""POST /api/v1/backtests, GET /api/v1/backtests/{id} (stub, UNCONFIRMED demo).

Phase 0: runs synchronously and returns a clearly-labeled synthetic result
immediately (status=done). When argus_quant.backtest is available the real
path is used; otherwise the stub path is taken and labeled. Research schema
only — a backtest never writes to production tables.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Dict

import numpy as np
from fastapi import APIRouter, Request

from app import data_stub
from app.quant_compat import QUANT_AVAILABLE, quant_attr
from app.routers import env, not_found
from app.schemas import BacktestMetrics, BacktestRequest, BacktestResult

router = APIRouter(tags=["backtests"])

_STORE: Dict[str, Dict] = {}


def _stub_backtest(cfg: BacktestRequest, backtest_id: str) -> Dict:
    seed = int(hashlib.sha256(backtest_id.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    n_days = 252
    rets = rng.normal(0.0004, 0.012, n_days)
    equity = (1 + rets).cumprod()
    curve = [
        {"date": f"2025-01-{(i % 28) + 1:02d}", "equity": round(float(e), 4),
         "benchmark": round(float(1 + 0.0003 * i), 4)}
        for i, e in enumerate(equity[::21])
    ]
    total = float(equity[-1] - 1)
    metrics = BacktestMetrics(
        cagr=round(float((1 + total) ** (252 / n_days) - 1), 4),
        total_return=round(total, 4),
        sharpe=round(float(rets.mean() / rets.std() * np.sqrt(252)), 3),
        sortino=round(float(rets.mean() / rets[rets < 0].std() * np.sqrt(252)), 3),
        max_drawdown=round(float((equity / np.maximum.accumulate(equity) - 1).min()), 4),
        win_rate=round(float((rets > 0).mean()), 3),
        profit_factor=round(float(rets[rets > 0].sum() / -rets[rets < 0].sum()), 3),
        expectancy=round(float(rets.mean()), 5),
        n_trades=int(rng.integers(20, 120)),
        benchmark="SPY",
    )
    return BacktestResult(
        backtest_id=backtest_id, name=cfg.name, status="done",
        config=cfg.model_dump(), metrics=metrics, equity_curve=curve,
        data_status="UNCONFIRMED",
        notes=[
            "DEMO/STUB: synthetic backtest on generated random returns — NOT "
            "a real strategy evaluation. Research schema only.",
        ],
    ).model_dump()


@router.post("/backtests", status_code=202)
def create_backtest(body: BacktestRequest, request: Request):
    backtest_id = uuid.uuid4().hex[:12]
    quant_bt = quant_attr("backtest")
    if QUANT_AVAILABLE and quant_bt is not None:
        try:
            payload = _quant_backtest(body, backtest_id, quant_bt)
        except Exception as exc:
            payload = _stub_backtest(body, backtest_id)
            payload["notes"].append(f"quant path failed ({exc}); used stub.")
    else:
        payload = _stub_backtest(body, backtest_id)
    _STORE[backtest_id] = payload
    return env({"backtest_id": backtest_id}, payload["data_status"], request)


def _equity_curve_points(equity_curve) -> list:
    """Downsample a quant equity Series to ~monthly points for the API."""
    import pandas as pd

    if not isinstance(equity_curve, pd.Series) or equity_curve.empty:
        return []
    return [
        {"date": ts.isoformat(), "equity": round(float(eq), 4)}
        for ts, eq in equity_curve.iloc[::21].items()
    ]


def _quant_backtest(cfg: BacktestRequest, backtest_id: str, quant_bt) -> Dict:
    """Real quant backtest on synthetic stub prices — DEMO ONLY (UNCONFIRMED).

    Phase 0: custom entry/exit rule translation is not implemented, so the
    quant path runs a fixed SMA(20/50) crossover demo strategy. Requests
    with custom rules fall back to the labeled stub.
    """
    import pandas as pd

    if cfg.entry_rules or cfg.exit_rules:
        raise ValueError(
            "custom entry/exit rule translation not implemented in Phase 0"
        )
    ticker = (cfg.universe or ["SPY"])[0].upper()
    bars = data_stub.synthetic_prices(
        ticker if data_stub.get_asset(ticker) else "SPY", 504
    )
    df = pd.DataFrame(bars)
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time").sort_index()

    def entry_rule(i, df_slice, state):
        if len(df_slice) < 51 or state["in_position"]:
            return 0
        c = df_slice["close"]
        return 1 if c.rolling(20).mean().iloc[-1] > c.rolling(50).mean().iloc[-1] else 0

    def exit_rule(i, df_slice, state):
        c = df_slice["close"]
        return c.rolling(20).mean().iloc[-1] < c.rolling(50).mean().iloc[-1]

    res = quant_bt.run_backtest(
        df,
        entry_rule,
        exit_rule,
        position_fraction=cfg.position_sizing.get("fraction", 0.10),
        stop_loss=cfg.stop_loss,
        take_profit=cfg.take_profit,
        slippage_bps=cfg.slippage_bps,
        commission=cfg.commission,
        allow_short=cfg.allow_short,
    )
    metrics = BacktestResult(
        backtest_id=backtest_id, name=cfg.name, status="done",
        config={**cfg.model_dump(), "strategy": "SMA(20/50) crossover (demo)"},
        metrics=BacktestMetrics(
            cagr=res.get("cagr"), total_return=res.get("total_return"),
            sharpe=res.get("sharpe"), sortino=res.get("sortino"),
            max_drawdown=res.get("max_drawdown"), win_rate=res.get("win_rate"),
            profit_factor=res.get("profit_factor"),
            expectancy=res.get("expectancy"), n_trades=res.get("n_trades"),
            benchmark="SPY",
        ),
        equity_curve=_equity_curve_points(res.get("equity_curve")),
        data_status="UNCONFIRMED",
        notes=[
            "DEMO: argus_quant.run_backtest on SYNTHETIC stub prices with a "
            "fixed SMA(20/50) crossover demo strategy — not a real strategy "
            "evaluation. Research schema only.",
        ],
    ).model_dump()
    return metrics


@router.get("/backtests/{backtest_id}")
def get_backtest(backtest_id: str, request: Request):
    payload = _STORE.get(backtest_id)
    if payload is None:
        not_found(f"backtest {backtest_id} not found")
    return env(payload, payload.get("data_status", "UNCONFIRMED"), request)
