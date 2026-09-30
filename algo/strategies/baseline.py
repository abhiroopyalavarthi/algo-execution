"""Worst-case baseline: try to do the whole order right away.

It asks for everything that's left on every bar, so it trades as fast as the
exchange's participation cap allows and pays the most impact.
"""

from __future__ import annotations

from algo.strategies.base import ExecutionAlgo


class Aggressive(ExecutionAlgo):
    name = "aggressive"

    def child_qty(self, i: int) -> int:
        return self.remaining
