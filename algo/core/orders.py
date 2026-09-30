"""Order, Fill, Side and Bar.

Prices crossing the exchange boundary are integer ticks (cents), same rule as
the order book: 0.1 + 0.2 != 0.3 in floating point, and an exchange that
compares prices with == can't afford that. Analytics convert back to floats.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum

import pandas as pd

TICK = 0.01  # one cent


def to_ticks(price: float) -> int:
    """Dollar price -> integer cents. round() rather than int() so 229.99999 -> 23000."""
    return int(round(price * 100))


def to_price(ticks: int) -> float:
    return ticks / 100.0


class Side(IntEnum):
    BUY = 1
    SELL = -1

    @property
    def sign(self) -> int:
        return int(self)

    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(Enum):
    MARKET = "market"  # takes liquidity this bar, remainder is dropped (IOC)
    LIMIT = "limit"  # rests until filled or cancelled


class OrderStatus(Enum):
    NEW = "new"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"  # cancelled by the strategy, or IOC remainder dropped


@dataclass(slots=True)
class Order:
    id: int
    symbol: str
    side: Side
    qty: int
    type: OrderType = OrderType.MARKET
    limit_px: int | None = None  # ticks, LIMIT only
    max_pov: float | None = None  # optional cap as a fraction of the bar's volume
    ts: pd.Timestamp | None = None  # when the strategy sent it
    filled: int = 0
    status: OrderStatus = OrderStatus.NEW

    @property
    def remaining(self) -> int:
        return self.qty - self.filled

    @property
    def is_open(self) -> bool:
        return self.status in (OrderStatus.NEW, OrderStatus.PARTIAL)


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: int
    symbol: str
    side: Side
    qty: int
    price: int  # ticks
    ts: pd.Timestamp


@dataclass(frozen=True, slots=True)
class Bar:
    """One bar of market data, prices in ticks.

    `typical` is (H+L+C)/3, the usual stand-in for the average trade price
    inside a bar when all you have is OHLCV. The fill model prices off it.
    """

    ts: pd.Timestamp
    symbol: str
    open: int
    high: int
    low: int
    close: int
    volume: int
    typical: int = field(default=0)

    @staticmethod
    def make(ts, symbol, o, h, l, c, v) -> Bar:  # noqa: E741
        oi, hi, li, ci = to_ticks(o), to_ticks(h), to_ticks(l), to_ticks(c)
        # integer division with rounding, done once here so the Python and
        # C++ exchanges are handed exactly the same number
        typical = (hi + li + ci + 1) // 3
        return Bar(ts, symbol, oi, hi, li, ci, int(v), typical)
