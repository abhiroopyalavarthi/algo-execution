"""Shared parent-order logic for TWAP, VWAP and POV.

A parent order is "buy qty shares of symbol between start and end". The algo
turns it into market child orders, one decision per bar. Subclasses only
decide how much should be done by each bar.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import time

import pandas as pd

from algo.core.orders import Bar, Fill, Side
from algo.core.strategy import Strategy


def in_window(ts: pd.Timestamp, start: time | None, end: time | None) -> bool:
    t = ts.time()
    return (start is None or t >= start) and (end is None or t < end)


class ExecutionAlgo(Strategy):
    """
    Deadline rule (`on_deadline`):
      "sweep"  - the child for the last bar asks for everything still left,
                 ignoring the algo's own pacing (the exchange cap still applies).
      "report" - never break the algo's own rule; unfilled shares are reported.
    Nothing is ever sent after the window ends.
    """

    name = "exec"

    def __init__(
        self,
        symbol: str,
        side: Side,
        qty: int,
        start: time | None = None,
        end: time | None = None,
        lot_size: int = 100,
        on_deadline: str = "sweep",
    ) -> None:
        super().__init__()
        if qty <= 0:
            raise ValueError("qty must be positive")
        if on_deadline not in ("sweep", "report"):
            raise ValueError("on_deadline must be 'sweep' or 'report'")
        self.symbol = symbol
        self.side = Side(side)
        self.qty = int(qty)
        self.start = start
        self.end = end
        self.lot_size = max(1, int(lot_size))
        self.on_deadline = on_deadline

        self.filled = 0
        self.fill_notional = 0  # sum of qty * price in ticks
        self.times: list[pd.Timestamp] = []  # the bars in the window
        self.child_log: list[tuple[pd.Timestamp, int]] = []  # (bar it's meant for, qty)

    @property
    def remaining(self) -> int:
        return self.qty - self.filled

    @property
    def avg_price(self) -> float | None:
        return self.fill_notional / self.filled / 100 if self.filled else None

    # --- hooks ------------------------------------------------------------
    def on_start(self) -> None:
        all_times = self.ctx.session_times(self.symbol)
        self.times = [t for t in all_times if in_window(t, self.start, self.end)]
        if not self.times:
            raise ValueError(f"no bars for {self.symbol} inside the order window")
        self._window_idx = {t: i for i, t in enumerate(self.times)}
        # for each bar, the timestamp of the bar after it
        self._next_ts = dict(zip(all_times, all_times[1:], strict=False))
        self.prepare()
        if all_times[0] == self.times[0]:
            self._send_for(0)

    def on_fill(self, fill: Fill) -> None:
        if fill.symbol == self.symbol:
            self.filled += fill.qty
            self.fill_notional += fill.qty * fill.price

    def on_bar(self, ts: pd.Timestamp, bars: Mapping[str, Bar]) -> None:
        bar = bars.get(self.symbol)
        if bar is None:
            return
        self.observe(self._window_idx.get(ts, -1), bar)
        # send a child only if the next bar is inside the window
        nxt = self._window_idx.get(self._next_ts.get(ts))
        if nxt is not None:
            self._send_for(nxt)

    def _send_for(self, i: int) -> None:
        if self.remaining <= 0:
            return
        last = i == len(self.times) - 1
        want = self.child_qty(i)
        if last and self.on_deadline == "sweep":
            want = self.remaining
        want = min(want, self.remaining)
        if want < self.remaining:
            want = (want // self.lot_size) * self.lot_size  # round lots except the tail
        if want <= 0:
            return
        self.ctx.send_order(self.symbol, self.side, want, max_pov=self.child_max_pov(i, last))
        self.child_log.append((self.times[i], want))

    # --- for subclasses -----------------------------------------------------
    def prepare(self) -> None:
        """Called once in on_start after self.times is known."""

    def observe(self, i: int, bar: Bar) -> None:
        """Called with each bar the algo sees. i = index in the window, -1 = before it."""

    def child_qty(self, i: int) -> int:
        """Shares to send for bar i (catch-up included)."""
        raise NotImplementedError

    def child_max_pov(self, i: int, last: bool) -> float | None:
        return None


class ScheduleAlgo(ExecutionAlgo):
    """An algo that follows a fixed schedule: target[i] = shares that should be
    done by the end of bar i. Missed shares roll into the next child."""

    def schedule(self) -> list[int]:
        raise NotImplementedError

    def prepare(self) -> None:
        self.target = self.schedule()
        assert len(self.target) == len(self.times)
        assert self.target[-1] == self.qty

    def child_qty(self, i: int) -> int:
        return self.target[i] - self.filled


def cumulative_targets(qty: int, weights: list[float]) -> list[int]:
    """Turn per-bar weights into integer cumulative targets that end at qty."""
    total = float(sum(weights))
    if total <= 0:
        raise ValueError("weights must sum to something positive")
    out, run = [], 0.0
    for w in weights:
        run += w
        out.append(int(qty * run / total))
    out[-1] = qty
    return out
