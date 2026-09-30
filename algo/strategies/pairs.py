"""Pairs / synthetic spread on daily bars.

spread_t = log(A_t) - beta * log(B_t), with beta from a rolling OLS of log(A)
on log(B) over the last `lookback` days. z = (spread - rolling mean) / rolling std.

  z >  entry  -> short the spread: sell A, buy beta-weighted B
  z < -entry  -> long the spread
  |z| < exit  -> close
  |z| > stop  -> close and stay out until |z| drops back under entry

Everything uses closes up to and including today, and the orders trade on the
next bar, so there's no lookahead.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

from algo.core.orders import Bar, Fill, Side
from algo.core.strategy import Strategy


def rolling_beta(log_a: np.ndarray, log_b: np.ndarray) -> tuple[float, float]:
    """OLS of log_a on log_b. Returns (beta, alpha)."""
    b_mean, a_mean = log_b.mean(), log_a.mean()
    var = ((log_b - b_mean) ** 2).sum()
    beta = float(((log_b - b_mean) * (log_a - a_mean)).sum() / var) if var > 0 else 1.0
    return beta, float(a_mean - beta * b_mean)


class PairsTrader(Strategy):
    name = "pairs"

    def __init__(
        self,
        sym_a: str,
        sym_b: str,
        lookback: int = 60,
        entry_z: float = 2.0,
        exit_z: float = 0.5,
        stop_z: float = 4.0,
        notional: float = 100_000.0,
    ) -> None:
        super().__init__()
        self.a, self.b = sym_a, sym_b
        self.lookback = lookback
        self.entry_z, self.exit_z, self.stop_z = entry_z, exit_z, stop_z
        self.notional = notional

        self.closes: dict[str, list[float]] = {sym_a: [], sym_b: []}
        self.pos = {sym_a: 0, sym_b: 0}
        self.cash = 0.0
        self.state = 0  # +1 long spread, -1 short spread, 0 flat
        self.stopped_out = False
        self.n_trades = 0
        self.history: list[dict] = []

    def on_fill(self, fill: Fill) -> None:
        self.pos[fill.symbol] += fill.side.sign * fill.qty
        self.cash -= fill.side.sign * fill.qty * fill.price / 100

    def on_bar(self, ts: pd.Timestamp, bars: Mapping[str, Bar]) -> None:
        if self.a not in bars or self.b not in bars:
            return
        pa, pb = bars[self.a].close / 100, bars[self.b].close / 100
        self.closes[self.a].append(pa)
        self.closes[self.b].append(pb)
        equity = self.cash + self.pos[self.a] * pa + self.pos[self.b] * pb
        row = {"ts": ts, "equity": equity, "z": np.nan, "beta": np.nan, "state": self.state}
        self.history.append(row)

        if len(self.closes[self.a]) < self.lookback:
            return
        la = np.log(self.closes[self.a][-self.lookback:])
        lb = np.log(self.closes[self.b][-self.lookback:])
        beta, alpha = rolling_beta(la, lb)
        spread = la - beta * lb - alpha
        sd = spread.std(ddof=1)
        if sd == 0:
            return
        z = float((spread[-1] - spread.mean()) / sd)
        row["z"], row["beta"] = z, beta

        if self.state != 0:
            if abs(z) < self.exit_z:
                self._target(0, pa, pb, beta)
            elif abs(z) > self.stop_z:
                self._target(0, pa, pb, beta)
                self.stopped_out = True
        else:
            if self.stopped_out and abs(z) < self.entry_z:
                self.stopped_out = False
            if not self.stopped_out and self.stop_z > abs(z) > self.entry_z:
                self._target(-1 if z > 0 else 1, pa, pb, beta)
        row["state"] = self.state

    def _target(self, state: int, pa: float, pb: float, beta: float) -> None:
        """Move to a spread position: +1 = long A / short B, -1 = the reverse."""
        if state == 0:
            tgt_a = tgt_b = 0
        else:
            tgt_a = state * int(self.notional / pa)
            tgt_b = -state * int(abs(beta) * self.notional / pb)
        for sym, tgt in ((self.a, tgt_a), (self.b, tgt_b)):
            diff = tgt - self.pos[sym]
            if diff:
                self.ctx.send_order(sym, Side.BUY if diff > 0 else Side.SELL, abs(diff))
        self.state = state
        self.n_trades += 1

    def report(self, capital: float | None = None) -> dict:
        h = pd.DataFrame(self.history).set_index("ts")
        capital = capital or 2 * self.notional
        rets = h["equity"].diff().fillna(0) / capital
        sharpe = float(np.sqrt(252) * rets.mean() / rets.std()) if rets.std() > 0 else 0.0
        curve = capital + h["equity"]
        drawdown = curve / curve.cummax() - 1
        return {
            "total_pnl": float(h["equity"].iloc[-1]),
            "sharpe": sharpe,
            "max_drawdown": float(drawdown.min()),
            "trades": self.n_trades,
            "history": h,
        }
