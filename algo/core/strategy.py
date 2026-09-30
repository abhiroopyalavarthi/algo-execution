"""Strategy base class.

Every algo is a plug-in with four hooks: on_start, on_bar, on_fill, on_timer
(plus on_end). It never sees the exchange. It gets a Context that can send and
cancel orders and set timers, which is the whole API.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

import pandas as pd

from algo.core.orders import Bar, Fill, Order, OrderType, Side

if TYPE_CHECKING:
    from algo.core.backtester import Backtester


class Context:
    """The strategy's handle on the outside world, owned by the backtester."""

    def __init__(self, bt: Backtester) -> None:
        self._bt = bt

    @property
    def now(self) -> pd.Timestamp | None:
        """Timestamp of the last bar the strategy has seen (None before the first)."""
        return self._bt.now

    def send_order(
        self,
        symbol: str,
        side: Side,
        qty: int,
        type: OrderType = OrderType.MARKET,
        limit_px: int | None = None,
        max_pov: float | None = None,
    ) -> Order:
        return self._bt.send_order(symbol, side, qty, type, limit_px, max_pov)

    def cancel(self, order_id: int) -> bool:
        return self._bt.cancel(order_id)

    def set_timer(self, ts: pd.Timestamp, tag: str = "") -> None:
        """on_timer(ts, tag) fires just before the first bar at or after ts."""
        self._bt.set_timer(ts, tag)

    def position(self, symbol: str) -> int:
        return self._bt.positions.get(symbol, 0)

    def session_times(self, symbol: str) -> list[pd.Timestamp]:
        """The bar timestamps the backtest will replay for a symbol. This is
        the trading calendar (known in advance), not market data."""
        return self._bt.session_times(symbol)


class Strategy:
    """Base class. Override the hooks you need."""

    name = "strategy"

    def __init__(self) -> None:
        self.ctx: Context | None = None

    def bind(self, ctx: Context) -> None:
        self.ctx = ctx

    def on_start(self) -> None:
        """Called once before the first bar. Orders sent here trade in the first bar."""

    def on_bar(self, ts: pd.Timestamp, bars: Mapping[str, Bar]) -> None:
        """Called after the exchange has processed the bars at ts. Orders sent
        here trade in the next bar."""

    def on_fill(self, fill: Fill) -> None:
        """Called for every fill, before on_bar for the same bar."""

    def on_timer(self, ts: pd.Timestamp, tag: str) -> None:
        """Called when a timer set with ctx.set_timer comes due."""

    def on_end(self) -> None:
        """Called once after the last bar."""
