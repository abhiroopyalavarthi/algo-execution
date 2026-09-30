"""CppExchange: same interface as SimExchange, matching done by the C++ order book.

One algo_cpp.BarExchange per symbol. Its price band (FastOrderBook needs one
up front) is set from the first bar: half to double the opening price. Orders
sent before a symbol's first bar are held here and handed over at that bar.

The order checks (self-cross, limit and market orders mixed, price band) are
done in C++ against the real book, so this class only keeps a dict of open
orders to report fills against.
"""

from __future__ import annotations

from algo.core.exchange import Exchange, FillModel, pov_bps
from algo.core.orders import Bar, Fill, Order, OrderStatus, OrderType, Side

try:
    import algo_cpp
except ImportError as e:  # pragma: no cover - depends on the build
    raise ImportError("algo_cpp isn't built. Install it with: uv sync --extra cpp") from e

_SIDE = {Side.BUY: algo_cpp.Side.Buy, Side.SELL: algo_cpp.Side.Sell}
_ERRORS = {
    1: "price outside the book's price band",
    2: "duplicate order id",
    3: "would trade against our own order",
    4: "can't mix resting limit orders and market orders on one symbol",
}


class CppExchange(Exchange):
    def __init__(self, model: FillModel | None = None) -> None:
        super().__init__(model)
        self._books: dict[str, algo_cpp.BarExchange] = {}
        self._early: dict[str, list[Order]] = {}  # sent before the symbol's first bar
        self._orders: dict[int, Order] = {}
        self._open: dict[str, dict[int, Order]] = {}
        self._mkt: dict[str, list[Order]] = {}  # market orders waiting for the next bar

    def _new_book(self, bar: Bar) -> algo_cpp.BarExchange:
        m = self.model
        lo, hi = max(1, bar.open // 2), bar.open * 2
        return algo_cpp.BarExchange(lo, hi, m.cap_bps, m.half_spread_ticks, m.depth_bps)

    def _submit(self, book: algo_cpp.BarExchange, order: Order) -> None:
        if order.type is OrderType.LIMIT:
            rc = book.add_limit(order.id, _SIDE[order.side], order.limit_px, order.qty)
        else:
            rc = book.add_market(order.id, _SIDE[order.side], order.qty, pov_bps(order.max_pov))
        if rc:
            raise ValueError(f"order {order.id}: {_ERRORS[rc]}")

    def send_order(self, order: Order) -> None:
        self._validate(order)
        if order.id in self._orders:
            raise ValueError(f"duplicate order id {order.id}")
        sym = order.symbol
        book = self._books.get(sym)
        if book is None:
            early = self._early.setdefault(sym, [])
            if early and (early[0].type is OrderType.LIMIT) != (order.type is OrderType.LIMIT):
                raise ValueError(f"order {order.id}: {_ERRORS[4]}")
            early.append(order)
        else:
            self._submit(book, order)
        self._orders[order.id] = order
        self._open.setdefault(sym, {})[order.id] = order
        if order.type is OrderType.MARKET:
            self._mkt.setdefault(sym, []).append(order)

    def cancel(self, order_id: int) -> bool:
        order = self._orders.get(order_id)
        if order is None or not order.is_open:
            return False
        book = self._books.get(order.symbol)
        if book is None:
            self._early[order.symbol].remove(order)
        elif not book.cancel(order_id):
            return False
        if order.type is OrderType.MARKET:
            self._mkt[order.symbol].remove(order)
        del self._open[order.symbol][order_id]
        order.status = OrderStatus.CANCELLED
        return True

    def open_orders(self, symbol: str | None = None) -> list[Order]:
        syms = [symbol] if symbol else list(self._open)
        return [o for s in syms for o in self._open.get(s, {}).values()]

    def process_bar(self, bar: Bar) -> None:
        sym = bar.symbol
        book = self._books.get(sym)
        if book is None:
            book = self._books[sym] = self._new_book(bar)
            for order in self._early.pop(sym, []):
                self._submit(book, order)

        raw = book.process_bar(bar.high, bar.low, bar.typical, bar.volume)
        if raw:
            orders, open_, emit, ts = self._orders, self._open[sym], self._on_fill, bar.ts
            for oid, qty, px in raw:
                order = orders[oid]
                order.filled += qty
                if order.filled == order.qty:
                    order.status = OrderStatus.FILLED
                    del open_[oid]
                else:
                    order.status = OrderStatus.PARTIAL
                emit(Fill(oid, sym, order.side, qty, px, ts))

        # market orders are IOC: anything still open after its bar is dropped
        mkt = self._mkt.get(sym)
        if mkt:
            open_ = self._open[sym]
            for order in mkt:
                if order.is_open:
                    order.status = OrderStatus.CANCELLED
                    del open_[order.id]
            mkt.clear()
