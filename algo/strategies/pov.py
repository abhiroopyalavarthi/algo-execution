"""POV (percent of volume): trade a fixed share of whatever the market trades.

After each bar the algo sends target_rate * (that bar's volume), plus any
shortfall versus target_rate * (all volume so far in the window). It can't
know the next bar's volume, so each child also carries max_pov = max_rate and
the exchange clips it against the volume of the bar it really trades in. That's
what keeps a quiet bar from pushing us over max_rate.
"""

from __future__ import annotations

from algo.core.orders import Bar
from algo.strategies.base import ExecutionAlgo


class POV(ExecutionAlgo):
    name = "pov"

    def __init__(self, *args, target_rate: float = 0.10, max_rate: float = 0.20,
                 on_deadline: str = "report", **kwargs) -> None:
        super().__init__(*args, on_deadline=on_deadline, **kwargs)
        if not 0 < target_rate <= max_rate <= 1:
            raise ValueError("need 0 < target_rate <= max_rate <= 1")
        self.target_rate = target_rate
        self.max_rate = max_rate
        self.window_volume = 0
        self.last_volume = 0

    def observe(self, i: int, bar: Bar) -> None:
        self.last_volume = bar.volume
        if i >= 0:
            self.window_volume += bar.volume

    def child_qty(self, i: int) -> int:
        behind = self.target_rate * self.window_volume - self.filled
        return int(self.target_rate * self.last_volume + max(0.0, behind))

    def child_max_pov(self, i: int, last: bool) -> float | None:
        if last and self.on_deadline == "sweep":
            return None
        return self.max_rate
