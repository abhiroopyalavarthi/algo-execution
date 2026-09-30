"""TWAP: the same number of shares every bar, following the clock."""

from __future__ import annotations

from algo.strategies.base import ScheduleAlgo


def twap_slices(qty: int, n: int, lot_size: int = 1) -> list[int]:
    """Split qty into n slices of whole lots, spread as evenly as possible.
    The odd shares that don't make a full lot go into the last slice.

    twap_slices(1050, 4, 100) -> [200, 300, 200, 350]

    (Putting *all* the rounding leftover in the last slice sounds simpler, but
    with 232,400 shares over 390 minutes that's 500 a minute and 37,900 in the
    last minute, which the last bar often can't absorb.)
    """
    if n <= 0:
        raise ValueError("need at least one slice")
    lots = qty // lot_size
    slices = [(lots * (i + 1) // n - lots * i // n) * lot_size for i in range(n)]
    slices[-1] += qty - lots * lot_size
    return slices


class TWAP(ScheduleAlgo):
    name = "twap"

    def schedule(self) -> list[int]:
        out, run = [], 0
        for s in twap_slices(self.qty, len(self.times), self.lot_size):
            run += s
            out.append(run)
        return out
