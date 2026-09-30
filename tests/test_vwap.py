import numpy as np
import pandas as pd
import pytest

from algo.core.backtester import run_backtest
from algo.core.orders import Side
from algo.strategies import VWAP, build_volume_curve


def test_curve_sums_to_one_and_is_u_shaped(history, last_day):
    curve = build_volume_curve(history, last_day, lookback=20)
    assert len(curve) == 390
    assert curve.sum() == pytest.approx(1.0)
    # open and close heavier than lunch
    assert curve.iloc[:15].mean() > 2 * curve.iloc[180:210].mean()
    assert curve.iloc[-15:].mean() > 2 * curve.iloc[180:210].mean()


def test_curve_uses_no_data_from_day_d_or_later(history):
    """The lookahead test. Wreck day D and every day after it: the curve for D can't change."""
    days = sorted(set(history.index.date))
    d = days[21]
    before = build_volume_curve(history, d, lookback=20)

    tampered = history.copy()
    later = np.array([x >= d for x in tampered.index.date])
    rng = np.random.default_rng(0)
    tampered.loc[later, "volume"] = rng.integers(1, 10_000_000, later.sum())
    after = build_volume_curve(tampered, d, lookback=20)
    pd.testing.assert_series_equal(before, after)

    # and the days it says it used are all before D
    assert all(pd.Timestamp(x).date() < d for x in before.attrs["days_used"])
    assert len(before.attrs["days_used"]) == 20

    # sanity check that the test can fail: tampering with an earlier day does move it
    tampered2 = history.copy()
    early = np.array([x == days[20] for x in tampered2.index.date])
    tampered2.loc[early, "volume"] = rng.integers(1, 10_000_000, early.sum())
    assert not build_volume_curve(tampered2, d, lookback=20).equals(before)


def test_not_enough_history_raises(history):
    days = sorted(set(history.index.date))
    with pytest.raises(ValueError):
        build_volume_curve(history, days[2], lookback=20, min_days=5)


def test_vwap_follows_curve_and_catches_up(history, last_day, day_bars):
    curve = build_volume_curve(history, last_day)
    qty = 300_000
    strat = VWAP("SYN", Side.BUY, qty, curve=curve)
    res = run_backtest({"SYN": day_bars}, strat)
    assert strat.filled == qty
    # more shares in the first 30 minutes than in 30 minutes around lunch
    f = res.fills_frame().set_index("ts")["qty"]
    first = f[f.index.time < pd.Timestamp("10:00").time()].sum()
    lunch = f[(f.index.time >= pd.Timestamp("12:30").time())
              & (f.index.time < pd.Timestamp("13:00").time())].sum()
    assert first > 2 * lunch
