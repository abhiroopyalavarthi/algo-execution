"""Market-making quoter with inventory skew.

Every bar: cancel the old quotes, then quote a bid and an ask around the
bar's close. When we're long, both quotes shift down (skew), so the ask is more
likely to get hit and the bid less likely, which pulls inventory back to zero.
Hard limit: a bid is sized so that even if it fills completely the position
can't go past max_position (same for the ask on the short side).
In the last `flatten_bars` bars it stops quoting and closes the position.
"""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from algo.core.orders import Bar, Fill, OrderType, Side
from algo.core.strategy import Strategy


class MarketMaker(Strategy):
    name = "market_maker"

    def __init__(
        self,
        symbol: str,
        half_spread_ticks: int = 2,
        quote_size: int = 100,
        max_position: int = 1_000,
        skew_ticks_per_lot: float = 1.0,
        flatten_bars: int = 1,
    ) -> None:
        super().__init__()
        self.symbol = symbol
        self.half_spread = half_spread_ticks
        self.quote_size = quote_size
        self.max_position = max_position
        self.skew = skew_ticks_per_lot
        self.flatten_bars = flatten_bars

        self.position = 0
        self.cash = 0  # ticks
        self.quoted = 0  # total shares quoted
        self.filled_qty = 0
        self.n_quotes = 0
        self._live: list[int] = []
        self.history: list[dict] = []

    def on_start(self) -> None:
        self.times = self.ctx.session_times(self.symbol)
        # from this bar on, stop quoting and send market orders to get flat
        k = self.flatten_bars + 1
        self._stop_at = self.times[-k] if len(self.times) >= k else self.times[0]

    def on_fill(self, fill: Fill) -> None:
        self.position += fill.side.sign * fill.qty
        self.cash -= fill.side.sign * fill.qty * fill.price
        self.filled_qty += fill.qty
        assert abs(self.position) <= self.max_position, "position limit broken"

    def on_bar(self, ts: pd.Timestamp, bars: Mapping[str, Bar]) -> None:
        bar = bars.get(self.symbol)
        if bar is None:
            return
        for oid in self._live:
            self.ctx.cancel(oid)
        self._live.clear()

        mid = bar.close
        self.history.append({"ts": ts, "position": self.position, "mid": mid / 100,
                             "pnl": (self.cash + self.position * mid) / 100})

        # the next bar is where we'd trade; stop quoting when that's the close
        if ts >= self._stop_at:
            if self.position != 0 and ts != self.times[-1]:
                side = Side.SELL if self.position > 0 else Side.BUY
                self.ctx.send_order(self.symbol, side, abs(self.position))
            return

        lots = self.position / self.quote_size
        reservation = mid - round(self.skew * lots)
        bid = reservation - self.half_spread
        ask = reservation + self.half_spread

        bid_qty = min(self.quote_size, self.max_position - self.position)
        ask_qty = min(self.quote_size, self.max_position + self.position)
        if bid_qty > 0 and bid > 0:
            self._quote(Side.BUY, bid_qty, bid)
        if ask_qty > 0:
            self._quote(Side.SELL, ask_qty, ask)

    def _quote(self, side: Side, qty: int, px: int) -> None:
        o = self.ctx.send_order(self.symbol, side, qty, OrderType.LIMIT, limit_px=px)
        self._live.append(o.id)
        self.quoted += qty
        self.n_quotes += 1

    def report(self) -> dict:
        h = pd.DataFrame(self.history).set_index("ts") if self.history else pd.DataFrame()
        pnl = h["pnl"] if len(h) else pd.Series(dtype=float)
        return {
            "pnl": float(pnl.iloc[-1]) if len(pnl) else 0.0,
            "max_abs_position": int(h["position"].abs().max()) if len(h) else 0,
            "final_position": self.position,
            "fill_rate": self.filled_qty / self.quoted if self.quoted else 0.0,
            "shares_traded": self.filled_qty,
            "quotes": self.n_quotes,
            "history": h,
        }
