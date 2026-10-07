"""Event-driven backtester.

Execution convention (documented, no lookahead):
    * At the close of bar ``i``, ``entry_rule`` / ``exit_rule`` are evaluated
      on ``df.iloc[:i+1]`` (data ``<= i`` only).
    * Fills happen at ``close[i]`` adjusted for slippage:
      buy  fill = close * (1 + slippage_bps/1e4)
      sell fill = close * (1 - slippage_bps/1e4)
    * Stop-loss / take-profit are evaluated against the bar's low/high and,
      when triggered, fill at the stop/take price (slippage still applies).
    * Commission is charged flat per fill (entry and exit).

Rule protocol:
    entry_rule(i, df_slice, state) -> int   # +1 long, -1 short, 0 nothing
    exit_rule(i, df_slice, state) -> bool   # True -> close position now
``state`` is a dict with keys: cash, shares (signed), entry_price,
entry_bar, equity, in_position (bool), side (+1/-1/0).

Accounting identity (tested): equity == cash + shares * close at every bar.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _slip(price: float, side: int, slippage_bps: float) -> float:
    # side +1 = buy (pay up), -1 = sell (give up)
    return price * (1.0 + side * slippage_bps / 1e4)


def run_backtest(df: pd.DataFrame, entry_rule, exit_rule,
                 initial_cash: float = 100_000.0,
                 position_fraction: float = 0.10,
                 stop_loss=None,
                 take_profit=None,
                 slippage_bps: float = 1.0,
                 commission: float = 1.0,
                 allow_short: bool = False,
                 spy_prices=None,
                 risk_free: float = 0.0,
                 on_bar=None) -> dict:
    """Run the event-driven backtest. Returns the full metrics dict
    (see DATABASE_SCHEMA research.backtest_metrics) plus ``equity_curve``
    and ``trades``.

    ``on_bar``: optional callable ``(i, timestamp, snapshot)`` invoked at
    the close of every bar with ``snapshot`` =
    {"cash", "shares", "close", "equity"}. Intended for audit hooks
    (e.g. asserting cash + shares*close == equity every bar)."""
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("df must have a DatetimeIndex")
    for c in ("open", "high", "low", "close"):
        if c not in df.columns:
            raise ValueError("df missing column %s" % c)

    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    n = len(df)

    cash = float(initial_cash)
    shares = 0.0
    entry_price = 0.0
    entry_bar = -1
    side = 0
    trades: list = []
    equity_curve = np.empty(n)
    exposure_bars = 0

    def equity_now(px):
        return cash + shares * px

    for i in range(n):
        px = close[i]
        df_slice = df.iloc[: i + 1]
        state = {"cash": cash, "shares": shares, "entry_price": entry_price,
                 "entry_bar": entry_bar, "equity": equity_now(px),
                 "in_position": shares != 0.0, "side": side}

        exit_now, exit_reason, exit_px = False, "", px
        if shares != 0.0:
            if stop_loss is not None:
                stop_px = entry_price * (1.0 - side * stop_loss)
                hit = (low[i] <= stop_px) if side == 1 else (high[i] >= stop_px)
                if hit:
                    exit_now, exit_reason, exit_px = True, "stop_loss", stop_px
            if not exit_now and take_profit is not None:
                take_px = entry_price * (1.0 + side * take_profit)
                hit = (high[i] >= take_px) if side == 1 else (low[i] <= take_px)
                if hit:
                    exit_now, exit_reason, exit_px = True, "take_profit", take_px
            if not exit_now and bool(exit_rule(i, df_slice, state)):
                exit_now, exit_reason, exit_px = True, "rule", px

        if exit_now:
            fill = _slip(exit_px, -side, slippage_bps)
            proceeds = shares * fill
            cash += proceeds - commission
            pnl = (fill - entry_price) * shares - 2 * commission
            trades.append({
                "entry_time": df.index[entry_bar], "exit_time": df.index[i],
                "side": int(side), "qty": abs(shares),
                "entry_price": round(entry_price, 4),
                "exit_price": round(fill, 4), "pnl": round(pnl, 2),
                "exit_reason": exit_reason, "bars_held": i - entry_bar,
            })
            shares, entry_price, entry_bar, side = 0.0, 0.0, -1, 0

        if shares == 0.0:
            state = {"cash": cash, "shares": 0.0, "entry_price": 0.0,
                     "entry_bar": -1, "equity": cash,
                     "in_position": False, "side": 0}
            want = int(entry_rule(i, df_slice, state))
            if want != 0 and (want == 1 or allow_short):
                notional = position_fraction * cash
                qty = int(notional // px)
                if qty > 0:
                    fill = _slip(px, want, slippage_bps)
                    cash -= want * qty * fill + commission
                    shares = want * qty
                    entry_price, entry_bar, side = fill, i, want
        else:
            exposure_bars += 1

        equity_curve[i] = equity_now(px)
        if on_bar is not None:
            on_bar(i, df.index[i],
                   {"cash": cash, "shares": shares, "close": px,
                    "equity": equity_now(px)})

    if shares != 0.0:
        fill = _slip(px, -side, slippage_bps)
        cash += shares * fill - commission
        pnl = (fill - entry_price) * shares - 2 * commission
        trades.append({
            "entry_time": df.index[entry_bar], "exit_time": df.index[-1],
            "side": int(side), "qty": abs(shares),
            "entry_price": round(entry_price, 4),
            "exit_price": round(fill, 4), "pnl": round(pnl, 2),
            "exit_reason": "end_of_data", "bars_held": n - 1 - entry_bar,
        })
        shares = 0.0
        equity_curve[-1] = cash

    equity = pd.Series(equity_curve, index=df.index, name="equity")
    rets = equity.pct_change().fillna(0.0)
    metrics = _metrics(equity, rets, trades, n, exposure_bars,
                       initial_cash, risk_free)
    metrics["equity_curve"] = equity
    metrics["trades"] = trades
    if spy_prices is not None:
        metrics.update(_benchmark_stats(rets, equity, spy_prices))
    return metrics


def _metrics(equity, rets, trades, n, exposure_bars, initial_cash,
             risk_free) -> dict:
    final = float(equity.iloc[-1])
    total_return = final / initial_cash - 1.0
    years = n / TRADING_DAYS
    cagr = (final / initial_cash) ** (1.0 / years) - 1.0 if years > 0 else 0.0

    excess = rets - risk_free / TRADING_DAYS
    vol = float(excess.std(ddof=1))
    sharpe = float(excess.mean() / vol * np.sqrt(TRADING_DAYS)) if vol > 0 else 0.0
    downside = excess[excess < 0]
    dvol = float(downside.std(ddof=1))
    sortino = (float(excess.mean() / dvol * np.sqrt(TRADING_DAYS))
               if dvol > 0 else 0.0)

    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = float(dd.min())

    pnls = np.array([t["pnl"] for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    n_trades = len(pnls)
    win_rate = float(len(wins) / n_trades) if n_trades else 0.0
    gross_win = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (
        float("inf") if gross_win > 0 else 0.0)
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    expectancy = float(pnls.mean()) if n_trades else 0.0
    recovery = abs(total_return / max_dd) if max_dd < 0 else 0.0

    monthly = equity.resample("ME").last().pct_change().dropna()
    monthly_returns = {d.strftime("%Y-%m"): round(float(v), 6)
                       for d, v in monthly.items()}

    pf = round(profit_factor, 4) if np.isfinite(profit_factor) else None
    return {
        "cagr": round(cagr, 6),
        "total_return": round(total_return, 6),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "max_drawdown": round(max_dd, 6),
        "win_rate": round(win_rate, 4),
        "profit_factor": pf,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "recovery_factor": round(recovery, 4),
        "n_trades": n_trades,
        "exposure": round(exposure_bars / n, 4) if n else 0.0,
        "monthly_returns": monthly_returns,
        "final_equity": round(final, 2),
    }


def _benchmark_stats(rets, equity, spy_prices) -> dict:
    """Alpha / beta / information ratio vs SPY (all on daily returns)."""
    spy = pd.Series(spy_prices, index=pd.DatetimeIndex(spy_prices.index))
    common = rets.index.intersection(spy.index)
    if len(common) < 30:
        return {"alpha": None, "beta": None, "information_ratio": None,
                "benchmark": "SPY",
                "benchmark_note": "insufficient overlap (<30 bars)"}
    s_rets = spy.loc[common].pct_change().fillna(0.0)
    r = rets.loc[common].to_numpy()
    b = s_rets.to_numpy()
    b_var = float(np.var(b, ddof=1))
    beta = float(np.cov(r, b, ddof=1)[0, 1] / b_var) if b_var > 0 else 0.0
    alpha = float(np.mean(r - beta * b) * TRADING_DAYS)
    active = r - b
    avol = float(np.std(active, ddof=1))
    ir = float(np.mean(active) / avol * np.sqrt(TRADING_DAYS)) if avol > 0 else 0.0
    bench_total = float(spy.loc[common].iloc[-1] / spy.loc[common].iloc[0] - 1.0)
    return {"alpha": round(alpha, 6), "beta": round(beta, 4),
            "information_ratio": round(ir, 4), "benchmark": "SPY",
            "benchmark_total_return": round(bench_total, 6)}
