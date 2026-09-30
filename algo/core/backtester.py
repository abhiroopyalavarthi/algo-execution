"""Event loop: replays bars in time order through an exchange and a strategy.

For each timestamp:
  1. fire any timers that are due (the strategy knows the time, not the bar)
  2. the exchange processes each symbol's bar -> fills -> strategy.on_fill
  3. strategy.on_bar sees the finished bars and may send orders for the next one
"""

from __future__ import annotations

import heapq
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from algo.core.exchange import Exchange, SimExchange
from algo.core.orders import Bar, Fill, Order, OrderType, Side
from algo.core.strategy import Context, Strategy


def frame_to_bars(symbol: str, df: pd.DataFrame) -> list[Bar]:
    """OHLCV DataFrame (float prices, DatetimeIndex) -> list of Bars in ticks."""
    o, h, lo, c = (np.rint(df[k].to_numpy() * 100).astype(np.int64) for k in
                   ("open", "high", "low", "close"))
    v = df["volume"].to_numpy().astype(np.int64)
    typ = (h + lo + c + 1) // 3
    idx = df.index
    return [
        Bar(idx[i], symbol, int(o[i]), int(h[i]), int(lo[i]), int(c[i]), int(v[i]), int(typ[i]))
        for i in range(len(df))
    ]


@dataclass
class BacktestResult:
    fills: list[Fill]
    orders: list[Order]
    bars: dict[str, pd.DataFrame]
    positions: dict[str, int]
    strategy: Strategy
    n_bars: int = 0
    extra: dict = field(default_factory=dict)

    def fills_frame(self) -> pd.DataFrame:
        if not self.fills:
            return pd.DataFrame(columns=["ts", "order_id", "symbol", "side", "qty", "price"])
        return pd.DataFrame(
            {
                "ts": [f.ts for f in self.fills],
                "order_id": [f.order_id for f in self.fills],
                "symbol": [f.symbol for f in self.fills],
                "side": [int(f.side) for f in self.fills],
                "qty": [f.qty for f in self.fills],
                "price": [f.price / 100 for f in self.fills],
            }
        )


class Backtester:
    def __init__(
        self,
        data: Mapping[str, pd.DataFrame],
        strategy: Strategy,
        exchange: Exchange | None = None,
    ) -> None:
        self.data = dict(data)
        self.strategy = strategy
        self.exchange = exchange or SimExchange()
        self.exchange.set_fill_handler(self._handle_fill)

        self.now: pd.Timestamp | None = None
        self.positions: dict[str, int] = defaultdict(int)
        self.fills: list[Fill] = []
        self.orders: list[Order] = []
        self._next_id = 1
        self._timers: list[tuple[pd.Timestamp, int, str]] = []
        self._timer_seq = 0

        # timeline: ts -> {symbol: Bar}
        by_ts: dict[pd.Timestamp, dict[str, Bar]] = defaultdict(dict)
        self._times: dict[str, list[pd.Timestamp]] = {}
        for sym in sorted(self.data):
            bars = frame_to_bars(sym, self.data[sym])
            self._times[sym] = [b.ts for b in bars]
            for b in bars:
                by_ts[b.ts][sym] = b
        self._timeline = sorted(by_ts.items(), key=lambda kv: kv[0])

    # --- used by Context ------------------------------------------------
    def send_order(self, symbol, side, qty, type=OrderType.MARKET, limit_px=None, max_pov=None):
        order = Order(self._next_id, symbol, Side(side), int(qty), type, limit_px, max_pov,
                      ts=self.now)
        self._next_id += 1
        self.exchange.send_order(order)
        self.orders.append(order)
        return order

    def cancel(self, order_id: int) -> bool:
        return self.exchange.cancel(order_id)

    def set_timer(self, ts: pd.Timestamp, tag: str) -> None:
        heapq.heappush(self._timers, (ts, self._timer_seq, tag))
        self._timer_seq += 1

    def session_times(self, symbol: str) -> list[pd.Timestamp]:
        return list(self._times.get(symbol, []))

    # ---------------------------------------------------------------------
    def _handle_fill(self, fill: Fill) -> None:
        self.fills.append(fill)
        self.positions[fill.symbol] += fill.side.sign * fill.qty
        self.strategy.on_fill(fill)

    def run(self) -> BacktestResult:
        strat = self.strategy
        strat.bind(Context(self))
        strat.on_start()
        timers = self._timers
        process = self.exchange.process_bar
        for ts, bars in self._timeline:
            while timers and timers[0][0] <= ts:
                t_ts, _, tag = heapq.heappop(timers)
                strat.on_timer(ts, tag)
            for bar in bars.values():
                process(bar)
            self.now = ts
            strat.on_bar(ts, bars)
        strat.on_end()
        return BacktestResult(
            fills=self.fills,
            orders=self.orders,
            bars=self.data,
            positions=dict(self.positions),
            strategy=strat,
            n_bars=sum(len(b) for _, b in self._timeline),
        )


def run_backtest(data, strategy, exchange=None) -> BacktestResult:
    return Backtester(data, strategy, exchange).run()
