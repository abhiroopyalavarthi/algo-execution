from datetime import time

import pytest

from algo.core.backtester import run_backtest
from algo.core.orders import Side
from algo.strategies import TWAP
from algo.strategies.twap import twap_slices


@pytest.mark.parametrize("qty,n,lot", [(1050, 4, 100), (200_037, 390, 100), (99, 5, 100),
                                       (7, 3, 1), (1_000_000, 17, 100)])
def test_slices_add_up(qty, n, lot):
    s = twap_slices(qty, n, lot)
    assert len(s) == n and sum(s) == qty
    assert all(x % lot == 0 for x in s[:-1])


def test_leftover_goes_to_last_slice():
    # 10 lots over 4 slices -> 2,3,2,3 lots, and the 50 odd shares go last
    assert twap_slices(1050, 4, 100) == [200, 300, 200, 350]


def test_slices_are_even():
    s = twap_slices(232_400, 390, 100)
    assert max(s) - min(s) <= 100


def test_uneven_order_fills_exactly(day_bars):
    qty = 200_037
    strat = TWAP("SYN", Side.BUY, qty)
    res = run_backtest({"SYN": day_bars}, strat)
    assert sum(f.qty for f in res.fills) == qty  # every share, none extra
    assert strat.filled == qty
    # children are round lots except possibly the last
    for _, q in strat.child_log[:-1]:
        assert q % 100 == 0


def test_window_respected(day_bars):
    strat = TWAP("SYN", Side.SELL, 50_000, start=time(10, 0), end=time(11, 0))
    res = run_backtest({"SYN": day_bars}, strat)
    ts = [f.ts.time() for f in res.fills]
    assert min(ts) >= time(10, 0) and max(ts) < time(11, 0)
    assert strat.filled == 50_000
    assert len(strat.times) == 60
