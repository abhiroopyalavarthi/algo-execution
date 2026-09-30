"""Exchange interface and the pure-Python simulated exchange.

Strategies only ever talk to an exchange through send_order / cancel and the
fill callback, so SimExchange and CppExchange (algo/core/cpp_exchange.py) are
interchangeable. Both implement the same fill model, described below and in
the README.

Fill model, per bar and per symbol
----------------------------------
Orders sent after bar t are handled during bar t+1, so a strategy can never
trade on a bar it has already seen.

1. Participation cap. Our fills on each side are capped at
   `max_participation` of the bar's volume (default 25%).
2. Resting limit orders fill only if the bar trades *through* them: a buy at P
   fills if the bar's low < P, a sell if the high > P. Touching the price isn't
   enough because we don't know our queue position. Fills are at the limit
   price, best price first, then oldest first.
3. Market orders walk a synthetic book built around the bar's typical price.
   The first level is `half_spread_ticks` away from typical, and each level
   holds `depth_pct` of the bar's volume, so a bigger order pays more per share
   (linear impact) and thin bars are more expensive than busy ones. The
   synthetic book is shared by every market order in the bar, and its total
   size is whatever is left of the participation cap.
4. A market order can carry `max_pov`, which clips it to that fraction of the
   volume of the bar it actually trades in (used by POV).
5. Whatever a market order can't fill in its bar is dropped (IOC).

A symbol can't have resting limit orders and market orders at the same time.
In a real book your market order could hit your own resting order; the model
doesn't handle self-trades, so it refuses instead.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from algo.core.orders import Bar, Fill, Order, OrderStatus, OrderType, Side

FillHandler = Callable[[Fill], None]


@dataclass(frozen=True)
class FillModel:
    max_participation: float = 0.25  # of each bar's volume, per side
    half_spread_ticks: int = 1  # distance from typical price to the first level
    depth_pct: float = 0.05  # size of each synthetic level, as a fraction of bar volume

    def __post_init__(self) -> None:
        if self.half_spread_ticks < 1:
            raise ValueError("half_spread_ticks must be >= 1")
        if not 0 < self.max_participation <= 1 or not 0 < self.depth_pct <= 1:
            raise ValueError("max_participation and depth_pct must be in (0, 1]")

    # Everything below is integer maths so both exchanges agree to the share.
    @property
    def cap_bps(self) -> int:
        return int(round(self.max_participation * 10_000))

    @property
    def depth_bps(self) -> int:
        return int(round(self.depth_pct * 10_000))

    def cap(self, volume: int) -> int:
        return volume * self.cap_bps // 10_000

    def level_size(self, volume: int) -> int:
        return max(1, volume * self.depth_bps // 10_000)


def pov_bps(max_pov: float | None) -> int:
    """max_pov as integer basis points, 0 meaning no cap."""
    return 0 if max_pov is None else int(round(max_pov * 10_000))


class OwnLimits:
    """Our open limit orders per symbol, with a cached best bid/ask so the
    self-cross check doesn't have to scan every order on every send."""

    def __init__(self) -> None:
        self.by_sym: dict[str, dict[int, Order]] = {}
        self._best: dict[str, tuple[int | None, int | None]] = {}

    def any(self, sym: str) -> bool:
        return bool(self.by_sym.get(sym))

    def best(self, sym: str) -> tuple[int | None, int | None]:
        b = self._best.get(sym)
        if b is None:
            bids = [o.limit_px for o in self.by_sym.get(sym, {}).values() if o.side is Side.BUY]
            asks = [o.limit_px for o in self.by_sym.get(sym, {}).values() if o.side is Side.SELL]
            b = self._best[sym] = (max(bids, default=None), min(asks, default=None))
        return b

    def check(self, order: Order) -> None:
        bid, ask = self.best(order.symbol)
        if order.side is Side.BUY and ask is not None and order.limit_px >= ask:
            raise ValueError(f"order {order.id} would trade against our own order")
        if order.side is Side.SELL and bid is not None and order.limit_px <= bid:
            raise ValueError(f"order {order.id} would trade against our own order")

    def add(self, order: Order) -> None:
        self.by_sym.setdefault(order.symbol, {})[order.id] = order
        b = self._best.get(order.symbol)
        if b is not None:
            bid, ask = b
            if order.side is Side.BUY:
                bid = order.limit_px if bid is None else max(bid, order.limit_px)
            else:
                ask = order.limit_px if ask is None else min(ask, order.limit_px)
            self._best[order.symbol] = (bid, ask)

    def remove(self, order: Order) -> None:
        self.by_sym.get(order.symbol, {}).pop(order.id, None)
        b = self._best.get(order.symbol)
        if b is not None and order.limit_px == (b[0] if order.side is Side.BUY else b[1]):
            self._best[order.symbol] = None  # recompute next time it's needed

    def values(self, sym: str):
        return self.by_sym.get(sym, {}).values()


class Exchange(ABC):
    """What every exchange has to provide. Nothing else is shared."""

    def __init__(self, model: FillModel | None = None) -> None:
        self.model = model or FillModel()
        self._on_fill: FillHandler = lambda fill: None

    def set_fill_handler(self, handler: FillHandler) -> None:
        self._on_fill = handler

    @abstractmethod
    def send_order(self, order: Order) -> None: ...

    @abstractmethod
    def cancel(self, order_id: int) -> bool: ...

    @abstractmethod
    def process_bar(self, bar: Bar) -> None:
        """Match everything that's waiting against one bar and emit fills."""

    @abstractmethod
    def open_orders(self, symbol: str | None = None) -> list[Order]: ...

    # shared checks ------------------------------------------------------
    @staticmethod
    def _validate(order: Order) -> None:
        if order.qty <= 0:
            raise ValueError(f"order {order.id}: qty must be positive")
        if order.type is OrderType.LIMIT:
            if order.limit_px is None or order.limit_px <= 0:
                raise ValueError(f"order {order.id}: limit order needs a positive limit_px")
            if order.max_pov is not None:
                raise ValueError(f"order {order.id}: max_pov only applies to market orders")


class SimExchange(Exchange):
    """The fill model in plain Python. Easy to read, and the reference the C++
    exchange is tested against."""

    def __init__(self, model: FillModel | None = None) -> None:
        super().__init__(model)
        self._orders: dict[int, Order] = {}
        self._resting: dict[str, list[Order]] = {}  # limit orders, arrival order
        self._own = OwnLimits()
        self._pending_mkt: dict[str, list[Order]] = {}  # market orders for the next bar

    def send_order(self, order: Order) -> None:
        self._validate(order)
        if order.id in self._orders:
            raise ValueError(f"duplicate order id {order.id}")
        sym = order.symbol
        if order.type is OrderType.LIMIT:
            if self._pending_mkt.get(sym):
                raise ValueError(f"{sym}: limit order while market orders are pending")
            self._own.check(order)
            self._own.add(order)
            self._resting.setdefault(sym, []).append(order)
        else:
            if self._own.any(sym):
                raise ValueError(f"{sym}: market order while limit orders are resting")
            self._pending_mkt.setdefault(sym, []).append(order)
        self._orders[order.id] = order

    def cancel(self, order_id: int) -> bool:
        order = self._orders.get(order_id)
        if order is None or not order.is_open:
            return False
        if order.type is OrderType.LIMIT:
            self._own.remove(order)
            # removed from self._resting lazily in process_bar
        else:
            self._pending_mkt[order.symbol].remove(order)
        order.status = OrderStatus.CANCELLED
        return True

    def open_orders(self, symbol: str | None = None) -> list[Order]:
        out = []
        for book in (self._resting, self._pending_mkt):
            for sym, orders in book.items():
                if symbol is None or sym == symbol:
                    out.extend(o for o in orders if o.is_open)
        return out

    # ------------------------------------------------------------------
    def process_bar(self, bar: Bar) -> None:
        m = self.model
        cap = m.cap(bar.volume)
        used = {Side.BUY: 0, Side.SELL: 0}

        resting = self._resting.get(bar.symbol)
        if resting:
            resting = [o for o in resting if o.is_open]  # drop cancelled ones
            # the bar's sellers sweep our bids from the top, then its buyers our asks
            bids = [o for o in resting if o.side is Side.BUY and o.limit_px > bar.low]
            bids.sort(key=lambda o: -o.limit_px)  # stable sort keeps time priority
            used[Side.BUY] = self._sweep(bids, cap, bar)
            asks = [o for o in resting if o.side is Side.SELL and o.limit_px < bar.high]
            asks.sort(key=lambda o: o.limit_px)
            used[Side.SELL] = self._sweep(asks, cap, bar)
            for o in resting:
                if not o.is_open:
                    self._own.remove(o)
            self._resting[bar.symbol] = [o for o in resting if o.is_open]

        pending = self._pending_mkt.get(bar.symbol)
        if pending:
            level_size = m.level_size(bar.volume)
            # how far each side of the synthetic book has been eaten this bar
            walked = {Side.BUY: 0, Side.SELL: 0}
            for order in pending:
                side = order.side
                want = order.remaining
                pb = pov_bps(order.max_pov)
                if pb:
                    want = min(want, bar.volume * pb // 10_000)
                want = min(want, cap - used[side] - walked[side])
                start = walked[side]
                done = 0
                while done < want:
                    pos = start + done
                    level = pos // level_size
                    room = level_size - pos % level_size
                    q = min(room, want - done)
                    px = bar.typical + side.sign * (m.half_spread_ticks + level)
                    self._fill(order, q, px, bar)
                    done += q
                walked[side] += done
                if order.is_open:  # IOC: drop what didn't fill
                    order.status = OrderStatus.CANCELLED
            self._pending_mkt[bar.symbol] = []

    def _sweep(self, orders: list[Order], budget: int, bar: Bar) -> int:
        used = 0
        for o in orders:
            if used >= budget:
                break
            q = min(o.remaining, budget - used)
            self._fill(o, q, o.limit_px, bar)
            used += q
        return used

    def _fill(self, order: Order, qty: int, price: int, bar: Bar) -> None:
        order.filled += qty
        order.status = OrderStatus.FILLED if order.remaining == 0 else OrderStatus.PARTIAL
        self._on_fill(Fill(order.id, order.symbol, order.side, qty, price, bar.ts))
