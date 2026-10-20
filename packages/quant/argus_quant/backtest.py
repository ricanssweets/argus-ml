"""Event-driven backtester.

Execution convention (documented, no lookahead):
    * At the close of bar ``i``, ``entry_rule`` / ``exit_rule`` are evaluated
      on ``df.iloc[:i+1]`` (data ``<= i`` only).
    * Fills happen at ``close[i]`` adjusted for slippage:
      buy  fill = close * (1 + slippage_bps/1e4)
      sell fill = close * (1 - slippage_bps/1e4)
    * Stop-loss / take-profit are evaluated against the bar's low/high. When
      the bar GAPS THROUGH the level, the fill is the bar's open (the worst
      realisable price), never the unreachable theoretical level:
        long  stop : fill = min(stop_px, open[i])
        short stop : fill = max(stop_px, open[i])
        long  take : fill = max(take_px, open[i])
        short take : fill = min(take_px, open[i])
      Slippage still applies on top. Intrabar priority: stop-loss first
      (conservative), then take-profit, then the exit rule.
    * Commission is charged flat per fill (entry and exit).
    * ANNUALISATION: ``bars_per_year`` (default 252 = daily session bars) is
      used for CAGR, Sharpe, Sortino, alpha and the information ratio. Pass
      ``bars_per_year=52`` for weekly bars, 12 for monthly, etc. When the
      parameter is left as ``None`` the value is INFERRED from the median
      spacing of the DataFrame index (documented in
      :func:`infer_bars_per_year`) and the value actually used is echoed in
      the result as ``bars_per_year`` -- annualisation is never silent.

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
# Kept as the documented default for daily session bars (backwards compatible).
DEFAULT_BARS_PER_YEAR = TRADING_DAYS


def infer_bars_per_year(index) -> float:
    """Bars per year implied by the median spacing of a DatetimeIndex.

    Daily / business-daily bars -> 252; weekly -> 52; monthly -> 12;
    quarterly -> 4; yearly -> 1. Intraday spacing (< 1 calendar day) is
    mapped on a 6.5-hour US session. Returns
    ``DEFAULT_BARS_PER_YEAR`` when the index is too short or unusable, so
    callers always get an explicit, documented number.
    """
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 3:
        return float(DEFAULT_BARS_PER_YEAR)
    # Resolution-agnostic: pandas 3 allows us/ms/s datetime64 units, so go
    # through Timedelta.total_seconds() instead of assuming nanoseconds.
    deltas = index.to_series().diff().dropna().dt.total_seconds() / 86_400.0
    med = float(deltas.median())
    if not np.isfinite(med) or med <= 0:
        return float(DEFAULT_BARS_PER_YEAR)
    if med < 1.0:
        # intraday: bars per session * sessions per year
        return float(TRADING_DAYS * max(1.0, round(6.5 / med)))
    if med <= 3.0:
        return 252.0
    if med <= 10.0:
        return 52.0
    if med <= 45.0:
        return 12.0
    if med <= 100.0:
        return 4.0
    if med <= 200.0:
        return 2.0
    return 1.0


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
                 on_bar=None,
                 bars_per_year: float | None = None,
                 allow_fractional_shares: bool = False) -> dict:
    """Run the event-driven backtest. Returns the full metrics dict
    (see DATABASE_SCHEMA research.backtest_metrics) plus ``equity_curve``
    and ``trades``.

    ``bars_per_year``: annualisation factor used by CAGR/Sharpe/Sortino/
    alpha/IR. ``None`` (default) infers it from the index spacing; the
    value actually used is echoed in the result. 252 = daily.

    ``allow_fractional_shares``: when False (default, backwards compatible)
    orders are truncated to whole shares and the realised exposure is
    reported per trade plus aggregated under ``position_sizing`` with a
    warning whenever it deviates > 5% from ``position_fraction``; when True
    the requested fraction is filled exactly.

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
    op = df["open"].to_numpy(dtype=float)
    n = len(df)
    ppy = float(bars_per_year) if bars_per_year is not None \
        else infer_bars_per_year(df.index)

    cash = float(initial_cash)
    shares = 0.0
    entry_price = 0.0
    entry_bar = -1
    entry_equity = 0.0
    entry_exposure = 0.0
    side = 0
    trades: list = []
    equity_curve = np.empty(n)
    exposure_bars = 0
    realized_exposures: list[float] = []
    sizing_warnings: list[str] = []

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
                    # Gap through the stop: the realisable price is the open.
                    if np.isfinite(op[i]) and op[i] > 0:
                        exit_px = min(stop_px, op[i]) if side == 1 \
                            else max(stop_px, op[i])
                    else:
                        exit_px = stop_px
                    exit_now, exit_reason = True, "stop_loss"
            if not exit_now and take_profit is not None:
                take_px = entry_price * (1.0 + side * take_profit)
                hit = (high[i] >= take_px) if side == 1 else (low[i] <= take_px)
                if hit:
                    # Gap through the target: filled at the open (better than
                    # the limit for a long, worse for a short).
                    if np.isfinite(op[i]) and op[i] > 0:
                        exit_px = max(take_px, op[i]) if side == 1 \
                            else min(take_px, op[i])
                    else:
                        exit_px = take_px
                    exit_now, exit_reason = True, "take_profit"
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
                "realized_exposure": round(entry_exposure, 4),
                "requested_exposure": position_fraction,
            })
            shares, entry_price, entry_bar, side = 0.0, 0.0, -1, 0
            entry_exposure = 0.0

        if shares == 0.0:
            state = {"cash": cash, "shares": 0.0, "entry_price": 0.0,
                     "entry_bar": -1, "equity": cash,
                     "in_position": False, "side": 0}
            # The rule protocol is +1 long / -1 short / 0 nothing. Anything
            # else used to flow straight into ``side``, so returning 7 opened
            # a 7x-leveraged position and returning -3 a 3x short.
            raw_want = entry_rule(i, df_slice, state)
            try:
                want = int(raw_want)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "entry_rule must return one of +1/-1/0 (int), got %r at "
                    "bar %d" % (raw_want, i)) from exc
            if want != raw_want:
                raise ValueError(
                    "entry_rule must return one of +1/-1/0 (int), got %r at "
                    "bar %d (non-integral)" % (raw_want, i))
            if want not in (-1, 0, 1):
                raise ValueError(
                    "entry_rule must return one of +1/-1/0 (int), got %r at "
                    "bar %d - refusing to use it as a position multiplier"
                    % (raw_want, i))
            if want != 0 and (want == 1 or allow_short):
                notional = position_fraction * cash
                fill = _slip(px, want, slippage_bps)
                if allow_fractional_shares:
                    qty = notional / fill if fill > 0 else 0.0
                else:
                    qty = float(int(notional // px))
                if qty > 0:
                    equity_at_entry = cash          # flat -> equity == cash
                    cash -= want * qty * fill + commission
                    shares = want * qty
                    entry_price, entry_bar, side = fill, i, want
                    entry_equity = equity_at_entry
                    entry_exposure = abs(qty) * fill / equity_at_entry \
                        if equity_at_entry > 0 else 0.0
                    realized_exposures.append(entry_exposure)
                    if position_fraction > 0 and \
                            abs(entry_exposure - position_fraction) / position_fraction > 0.05:
                        sizing_warnings.append(
                            "bar %d: requested %.1f%% of cash, realised %.1f%% "
                            "(whole-share truncation)" % (
                                i, position_fraction * 100.0,
                                entry_exposure * 100.0))
                elif position_fraction > 0:
                    sizing_warnings.append(
                        "bar %d: requested %.1f%% of cash but not even one "
                        "whole share is affordable (price %.2f) - no entry"
                        % (i, position_fraction * 100.0, px))
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
            "realized_exposure": round(entry_exposure, 4),
            "requested_exposure": position_fraction,
        })
        shares = 0.0
        equity_curve[-1] = cash

    equity = pd.Series(equity_curve, index=df.index, name="equity")
    rets = equity.pct_change().fillna(0.0)
    metrics = _metrics(equity, rets, trades, n, exposure_bars,
                       initial_cash, risk_free, bars_per_year=ppy)
    metrics["bars_per_year"] = ppy
    metrics["position_sizing"] = {
        "requested_fraction": position_fraction,
        "allow_fractional_shares": bool(allow_fractional_shares),
        "n_entries": len(realized_exposures),
        "mean_realized_exposure": (round(float(np.mean(realized_exposures)), 4)
                                   if realized_exposures else None),
        "min_realized_exposure": (round(float(np.min(realized_exposures)), 4)
                                  if realized_exposures else None),
        "warnings": sizing_warnings,
    }
    metrics["equity_curve"] = equity
    metrics["trades"] = trades
    if spy_prices is not None:
        metrics.update(_benchmark_stats(rets, equity, spy_prices, ppy,
                                        risk_free=risk_free))
    return metrics


def _metrics(equity, rets, trades, n, exposure_bars, initial_cash,
             risk_free, bars_per_year: float | None = None) -> dict:
    ppy = float(bars_per_year) if bars_per_year is not None \
        else float(DEFAULT_BARS_PER_YEAR)
    final = float(equity.iloc[-1])
    total_return = final / initial_cash - 1.0
    years = n / ppy
    cagr = (final / initial_cash) ** (1.0 / years) - 1.0 if years > 0 else 0.0

    excess = rets - risk_free / ppy
    vol = float(excess.std(ddof=1))
    # "Not computable" is reported as None, never 0.0: a flat/no-trade curve
    # used to publish sharpe=0.0, sortino=0.0, profit_factor=0.0,
    # win_rate=0.0, recovery_factor=0.0 -- indistinguishable from a real
    # zero. None means "no evidence", which is what these cases are.
    sharpe = float(excess.mean() / vol * np.sqrt(ppy)) if vol > 0 else None
    # Target downside deviation: DD = sqrt(mean_over_all_N(min(r - MAR, 0)^2))
    # with MAR = 0 per period (RFC-style), NOT the std of the negative subset.
    dd = float(np.sqrt(np.mean(np.minimum(excess.to_numpy(dtype=float),
                                          0.0) ** 2)))
    sortino = (float(excess.mean() / dd * np.sqrt(ppy))
               if dd > 1e-12 else None)

    peak = equity.cummax()
    dd_eq = (equity - peak) / peak
    max_dd = float(dd_eq.min())

    pnls = np.array([t["pnl"] for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    n_trades = len(pnls)
    win_rate = float(len(wins) / n_trades) if n_trades else None
    gross_win = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    if gross_win <= 0 and gross_loss <= 0:
        profit_factor = None                    # no trades at all
    elif gross_loss > 0:
        profit_factor = gross_win / gross_loss
    else:
        profit_factor = None                    # no losing trade -> undefined
    avg_win = float(wins.mean()) if len(wins) else None
    avg_loss = float(losses.mean()) if len(losses) else None
    expectancy = float(pnls.mean()) if n_trades else None
    recovery = abs(total_return / max_dd) if max_dd < 0 else None

    monthly = equity.resample("ME").last().pct_change().dropna()
    monthly_returns = {d.strftime("%Y-%m"): round(float(v), 6)
                       for d, v in monthly.items()}

    pf = round(profit_factor, 4) if profit_factor is not None else None

    def _r(v, nd):
        return None if v is None else round(float(v), nd)

    return {
        "cagr": round(cagr, 6),
        "total_return": round(total_return, 6),
        "sharpe": _r(sharpe, 4),
        "sortino": _r(sortino, 4),
        "max_drawdown": round(max_dd, 6),
        "win_rate": _r(win_rate, 4),
        "profit_factor": pf,
        "avg_win": _r(avg_win, 2),
        "avg_loss": _r(avg_loss, 2),
        "expectancy": _r(expectancy, 2),
        "recovery_factor": _r(recovery, 4),
        "n_trades": n_trades,
        "exposure": round(exposure_bars / n, 4) if n else 0.0,
        "monthly_returns": monthly_returns,
        "final_equity": round(final, 2),
        "metrics_note": ("None means not computable with the available "
                         "observations (no trades / no downside / no "
                         "dispersion), never a fabricated 0.0"),
    }


def _benchmark_stats(rets, equity, spy_prices,
                     bars_per_year: float | None = None,
                     risk_free: float = 0.0) -> dict:
    """Jensen alpha / beta / information ratio vs SPY, annualised with
    ``bars_per_year`` (same factor the rest of the metrics use).

    ALPHA IS RISK-FREE ADJUSTED: ``alpha = ppy*[(mean(r) - rf_d) -
    beta*(mean(b) - rf_d)]`` with ``rf_d = risk_free/ppy``. The previous
    implementation reported ``ppy*mean(r - beta*b)``, i.e. Jensen alpha with
    rf assumed 0, while ``run_backtest`` accepted ``risk_free`` and used it
    only for Sharpe -- overstating skill by ~(1-beta)*rf per year.

    BENCHMARK RETURNS ARE COMPUTED ON THE BENCHMARK'S OWN CALENDAR: the
    benchmark series is reindexed onto the strategy calendar FIRST, so each
    used observation is a genuine 1-bar return on BOTH legs. Computing
    ``pct_change()`` after intersecting the two calendars collapsed several
    sessions into one multi-day observation and mixed horizons in
    beta/alpha/IR (measured: dropping 5 benchmark sessions moved beta by
    0.0021 and alpha by +0.0031 with the old code, vs 0.00007 / 0.0000
    now). Bars where the benchmark has no genuine 1-bar return (a missing
    session, or the first bar) are EXCLUDED rather than filled with a
    fabricated 0.0.

    The information ratio stays defined as active return over tracking error
    (excess vs the benchmark, not vs rf) -- see ``benchmark_note``.
    """
    ppy = float(bars_per_year) if bars_per_year is not None \
        else float(DEFAULT_BARS_PER_YEAR)
    spy = pd.Series(spy_prices, index=pd.DatetimeIndex(spy_prices.index))
    spy = spy.sort_index()
    # Reindex the benchmark onto the STRATEGY calendar first, then take the
    # return: a used observation therefore covers exactly one bar on both
    # sides (never a multi-day benchmark return against a 1-day strategy
    # return).
    aligned = spy.reindex(rets.index)
    present = aligned.notna().to_numpy()
    prev_present = np.concatenate([[False], present[:-1]]) if len(present) \
        else np.zeros(0, dtype=bool)
    bench_ret = aligned.pct_change().to_numpy()
    usable = present & prev_present & np.isfinite(bench_ret)
    n_used = int(usable.sum())
    n_gaps = int((~present).sum())
    if n_used < 30:
        return {"alpha": None, "beta": None, "information_ratio": None,
                "benchmark": "SPY",
                "benchmark_note": "insufficient overlap (<30 usable bars)",
                "n_obs_used": n_used, "n_benchmark_gaps": n_gaps}
    r = rets.to_numpy()[usable]
    b = bench_ret[usable]
    b_var = float(np.var(b, ddof=1))
    beta = float(np.cov(r, b, ddof=1)[0, 1] / b_var) if b_var > 0 else 0.0
    rf_d = float(risk_free) / ppy
    alpha = float(((np.mean(r) - rf_d) - beta * (np.mean(b) - rf_d)) * ppy)
    active = r - b
    avol = float(np.std(active, ddof=1))
    ir = float(np.mean(active) / avol * np.sqrt(ppy)) if avol > 0 else None
    bench_win = spy.loc[(spy.index >= rets.index[0])
                        & (spy.index <= rets.index[-1])]
    bench_total = (float(bench_win.iloc[-1] / bench_win.iloc[0] - 1.0)
                   if len(bench_win) >= 2 else None)
    return {"alpha": round(alpha, 6), "beta": round(beta, 4),
            "information_ratio": (round(ir, 4) if ir is not None else None),
            "benchmark": "SPY",
            "risk_free": float(risk_free),
            "benchmark_total_return": (round(bench_total, 6)
                                       if bench_total is not None else None),
            "benchmark_excess_return": (round(float(np.mean(b)) * ppy
                                              - float(risk_free), 6)),
            "n_obs_used": n_used,
            "n_benchmark_gaps": n_gaps,
            "benchmark_note": ("alpha = annualised Jensen alpha with "
                               "rf = risk_free; information_ratio = excess "
                               "vs benchmark / tracking error; benchmark "
                               "returns computed on its own calendar and "
                               "reindexed to the strategy calendar before "
                               "any pct_change; bars without a genuine "
                               "1-bar benchmark return are excluded")}
