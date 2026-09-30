"""Transaction cost analysis.

Sign convention: slippage = side * (avg fill - benchmark) / benchmark, in bps.
For a buy, positive means we paid more than the benchmark (bad). For a sell,
positive means we sold for less (also bad). So positive is always a cost.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from algo.core.orders import Fill, Side


def slippage_bps(side: Side, avg_px: float, benchmark: float) -> float:
    return Side(side).sign * (avg_px - benchmark) / benchmark * 1e4


def market_vwap(bars: pd.DataFrame) -> float:
    """Interval VWAP from bars, using each bar's typical price as its average trade price."""
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3
    return float((typical * bars["volume"]).sum() / bars["volume"].sum())


@dataclass
class TCAReport:
    algo: str
    symbol: str
    side: str
    qty: int
    filled: int
    avg_px: float
    arrival_px: float
    interval_vwap: float
    arrival_slippage_bps: float
    vwap_slippage_bps: float
    shortfall_bps: float  # arrival slippage on filled shares + cost of unfilled shares
    participation: float
    max_bar_participation: float
    tracking_error: float  # RMS gap between our cumulative % done and the market's
    n_fills: int

    @property
    def fill_rate(self) -> float:
        return self.filled / self.qty if self.qty else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["fill_rate"] = self.fill_rate
        return d

    def pretty(self) -> str:
        lines = [
            f"{self.algo.upper()}  {self.side} {self.qty:,} {self.symbol}",
            f"  filled            {self.filled:,} ({self.fill_rate:.1%}) in {self.n_fills} fills",
            f"  avg fill price    {self.avg_px:.4f}",
            f"  arrival price     {self.arrival_px:.4f}   slippage {self.arrival_slippage_bps:+.2f} bps",
            f"  interval VWAP     {self.interval_vwap:.4f}   slippage {self.vwap_slippage_bps:+.2f} bps",
            f"  impl. shortfall   {self.shortfall_bps:+.2f} bps",
            f"  participation     {self.participation:.2%} (max bar {self.max_bar_participation:.2%})",
            f"  tracking error    {self.tracking_error:.2%} vs market volume curve",
        ]
        return "\n".join(lines)


def analyze(
    algo: str,
    symbol: str,
    side: Side,
    qty: int,
    fills: list[Fill],
    window_bars: pd.DataFrame,
) -> TCAReport:
    """TCA for one parent order.

    window_bars: the market's bars over the order window (float prices).
    Arrival price is the first bar's open, i.e. the market when the order started.
    """
    side = Side(side)
    fills = [f for f in fills if f.symbol == symbol]
    filled = sum(f.qty for f in fills)
    avg_px = sum(f.qty * f.price for f in fills) / filled / 100 if filled else float("nan")

    arrival = float(window_bars["open"].iloc[0])
    ivwap = market_vwap(window_bars)
    end_px = float(window_bars["close"].iloc[-1])
    mkt_vol = window_bars["volume"].to_numpy(dtype=float)
    total_vol = mkt_vol.sum()

    # our volume per bar, lined up with the market's
    ours = pd.Series(0.0, index=window_bars.index)
    for f in fills:
        if f.ts in ours.index:
            ours[f.ts] += f.qty
    ours_arr = ours.to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        bar_part = np.where(mkt_vol > 0, ours_arr / mkt_vol, 0.0)

    # cumulative % of the order done vs cumulative % of the day's volume
    if filled:
        ours_cum = np.cumsum(ours_arr) / qty
        mkt_cum = np.cumsum(mkt_vol) / total_vol
        tracking = float(np.sqrt(np.mean((ours_cum - mkt_cum) ** 2)))
    else:
        tracking = float("nan")

    if filled:
        arr_slip = slippage_bps(side, avg_px, arrival)
        vwap_slip = slippage_bps(side, avg_px, ivwap)
    else:
        arr_slip = vwap_slip = float("nan")
    # implementation shortfall: filled shares vs arrival + unfilled shares marked at the close
    cost = sum(f.qty * (f.price / 100 - arrival) for f in fills) + (qty - filled) * (end_px - arrival)
    shortfall = side.sign * cost / (qty * arrival) * 1e4

    return TCAReport(
        algo=algo,
        symbol=symbol,
        side=side.name,
        qty=qty,
        filled=filled,
        avg_px=avg_px,
        arrival_px=arrival,
        interval_vwap=ivwap,
        arrival_slippage_bps=arr_slip,
        vwap_slippage_bps=vwap_slip,
        shortfall_bps=shortfall,
        participation=filled / total_vol if total_vol else 0.0,
        max_bar_participation=float(bar_part.max()) if len(bar_part) else 0.0,
        tracking_error=tracking,
        n_fills=len(fills),
    )
