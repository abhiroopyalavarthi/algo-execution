"""Glue: build an algo for one parent order, run it, grade it."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import time

import pandas as pd

from algo.analytics.tca import TCAReport, analyze
from algo.core.backtester import BacktestResult, run_backtest
from algo.core.exchange import Exchange, FillModel, SimExchange
from algo.core.orders import Side
from algo.strategies import POV, TWAP, VWAP, Aggressive, build_volume_curve
from algo.strategies.base import in_window

EXEC_ALGOS = ("twap", "vwap", "pov", "aggressive")


def make_exchange(kind: str = "sim", model: FillModel | None = None) -> Exchange:
    if kind == "sim":
        return SimExchange(model)
    if kind == "cpp":
        from algo.core.cpp_exchange import CppExchange

        return CppExchange(model)
    raise ValueError(f"unknown exchange {kind!r}")


def forecast_volume(history: pd.DataFrame, day, lookback: int = 20) -> float:
    """Average daily volume over the sessions before `day`."""
    day = pd.Timestamp(day).date()
    hist = history[history.index.date < day]
    daily = hist.groupby(hist.index.date)["volume"].sum()
    return float(daily.iloc[-lookback:].mean())


@dataclass
class OrderRun:
    result: BacktestResult
    tca: TCAReport
    day_bars: pd.DataFrame
    window_bars: pd.DataFrame


def run_order(
    algo: str,
    symbol: str,
    day,
    qty: int,
    side: Side = Side.BUY,
    history: pd.DataFrame | None = None,
    start: time | None = None,
    end: time | None = None,
    exchange: str | Exchange = "sim",
    model: FillModel | None = None,
    lookback: int = 20,
    pov_rate: float | None = None,
    lot_size: int = 100,
) -> OrderRun:
    """Run one parent order on one day.

    history: minute bars for this symbol covering `day` and the days before it
    (VWAP builds its curve from the earlier days, POV sizes itself from them).
    """
    if history is None:
        from algo.data.loader import load_minute

        history = load_minute(symbol)
    day_ts = pd.Timestamp(day).date()
    day_bars = history[history.index.date == day_ts]
    if day_bars.empty:
        raise ValueError(f"no bars for {symbol} on {day_ts}")

    common = dict(symbol=symbol, side=side, qty=qty, start=start, end=end, lot_size=lot_size)
    if algo == "twap":
        strat = TWAP(**common)
    elif algo == "vwap":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            curve = build_volume_curve(history, day_ts, lookback=lookback)
        strat = VWAP(**common, curve=curve)
    elif algo == "pov":
        if pov_rate is None:
            # pace the order to finish around the end of the window on a normal day
            adv = forecast_volume(history, day_ts, lookback)
            window_share = _window_share(history, day_ts, start, end, lookback)
            pov_rate = min(0.5, 1.1 * qty / max(1.0, adv * window_share))
        strat = POV(**common, target_rate=pov_rate, max_rate=min(1.0, 2 * pov_rate),
                    on_deadline="sweep")
    elif algo == "aggressive":
        strat = Aggressive(**common)
    else:
        raise ValueError(f"unknown algo {algo!r}; pick one of {EXEC_ALGOS}")

    ex = exchange if isinstance(exchange, Exchange) else make_exchange(exchange, model)
    result = run_backtest({symbol: day_bars}, strat, ex)
    mask = [in_window(t, start, end) for t in day_bars.index]
    window_bars = day_bars[mask]
    tca = analyze(algo, symbol, side, qty, result.fills, window_bars)
    return OrderRun(result, tca, day_bars, window_bars)


def _window_share(history, day, start, end, lookback) -> float:
    if start is None and end is None:
        return 1.0
    hist = history[history.index.date < day]
    days = sorted(set(hist.index.date))[-lookback:]
    hist = hist[pd.Index(hist.index.date).isin(days)]
    mask = [in_window(t, start, end) for t in hist.index]
    tot = hist["volume"].sum()
    return float(hist["volume"][mask].sum() / tot) if tot else 1.0
