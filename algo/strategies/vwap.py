"""VWAP: trade in proportion to the usual intraday volume curve.

The curve for day D is the average share of daily volume traded in each minute
of the day, over the `lookback` trading days *before* D. Day D itself is never
used, it hasn't happened yet when the order starts. tests/test_vwap.py checks
this by changing day D's data and showing the curve doesn't move.
"""

from __future__ import annotations

import warnings
from datetime import date

import numpy as np
import pandas as pd

from algo.strategies.base import ScheduleAlgo, cumulative_targets

SESSION_MINUTES = 390  # 09:30 to 16:00


def minute_of_day(index: pd.DatetimeIndex) -> np.ndarray:
    return (index.hour * 60 + index.minute - (9 * 60 + 30)).to_numpy()


def build_volume_curve(
    bars: pd.DataFrame,
    day: date | str | pd.Timestamp,
    lookback: int = 20,
    min_days: int = 5,
) -> pd.Series:
    """Volume curve for `day` from the `lookback` sessions strictly before it.

    bars: minute bars for one symbol, many days, NY-time DatetimeIndex.
    Returns a Series indexed by minute of day (0..389) that sums to 1.
    """
    day = pd.Timestamp(day).date()
    dates = bars.index.date
    prior_days = sorted({d for d in dates if d < day})[-lookback:]
    if len(prior_days) < min_days:
        raise ValueError(f"only {len(prior_days)} days of history before {day}, need {min_days}")
    if len(prior_days) < lookback:
        warnings.warn(
            f"VWAP curve for {day} built from {len(prior_days)} days (wanted {lookback})",
            stacklevel=2,
        )
    hist = bars[np.isin(dates, prior_days)]
    mins = minute_of_day(hist.index)
    keep = (mins >= 0) & (mins < SESSION_MINUTES)
    hist, mins = hist[keep], mins[keep]

    # each day's volume as a fraction of that day's total, then averaged over days,
    # so one huge day can't dominate the shape
    vol = hist["volume"].to_numpy(dtype=float)
    day_of = np.array(hist.index.date)
    frac = np.zeros(SESSION_MINUTES)
    for d in prior_days:
        m = day_of == d
        tot = vol[m].sum()
        if tot > 0:
            np.add.at(frac, mins[m], vol[m] / tot)
    frac /= len(prior_days)
    # a minute with no volume in any day would get zero weight forever
    frac = np.maximum(frac, 1e-6)
    curve = pd.Series(frac / frac.sum(), index=pd.RangeIndex(SESSION_MINUTES, name="minute"))
    curve.attrs["days_used"] = [str(d) for d in prior_days]
    return curve


class VWAP(ScheduleAlgo):
    name = "vwap"

    def __init__(self, *args, curve: pd.Series, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.curve = curve

    def schedule(self) -> list[int]:
        mins = minute_of_day(pd.DatetimeIndex(self.times))
        weights = self.curve.reindex(mins).fillna(0).to_numpy()
        if weights.sum() <= 0:
            weights = np.ones(len(self.times))
        # renormalised over just the order's window
        return cumulative_targets(self.qty, list(weights))
