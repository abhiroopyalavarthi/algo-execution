import pandas as pd
import pytest

from algo.analytics.tca import analyze, slippage_bps
from algo.core.orders import Fill, Side

from .conftest import make_bars


def test_three_fill_example_by_hand():
    # market: 3 bars
    #   typical prices 100.00, 101.00, 102.00; volumes 1000, 2000, 1000
    #   interval VWAP = (100*1000 + 101*2000 + 102*1000) / 4000 = 101.00
    #   arrival = first open = 99.50
    bars = make_bars([
        (99.50, 100.50, 99.50, 100.00, 1000),   # (100.5+99.5+100)/3 = 100
        (100.00, 101.50, 100.50, 101.00, 2000),  # 101
        (101.00, 102.50, 101.50, 102.00, 1000),  # 102
    ])
    ts = bars.index
    fills = [
        Fill(1, "X", Side.BUY, 100, 10010, ts[0]),  # 100.10
        Fill(2, "X", Side.BUY, 200, 10120, ts[1]),  # 101.20
        Fill(3, "X", Side.BUY, 100, 10200, ts[2]),  # 102.00
    ]
    # avg = (100*100.10 + 200*101.20 + 100*102.00) / 400 = 101.125
    r = analyze("test", "X", Side.BUY, 400, fills, bars)
    assert r.avg_px == pytest.approx(101.125)
    assert r.interval_vwap == pytest.approx(101.0)
    assert r.arrival_px == pytest.approx(99.5)
    assert r.vwap_slippage_bps == pytest.approx((101.125 - 101.0) / 101.0 * 1e4)  # +12.376
    assert r.vwap_slippage_bps == pytest.approx(12.376, abs=1e-3)
    assert r.arrival_slippage_bps == pytest.approx((101.125 - 99.5) / 99.5 * 1e4)  # +163.3
    assert r.participation == pytest.approx(400 / 4000)
    assert r.max_bar_participation == pytest.approx(0.1)
    # our cumulative 25%, 75%, 100% vs market 25%, 75%, 100% -> perfect tracking
    assert r.tracking_error == pytest.approx(0.0)
    assert r.fill_rate == 1.0
    assert r.shortfall_bps == pytest.approx(r.arrival_slippage_bps)


def test_sign_convention():
    # buy above the benchmark = paid more = positive
    assert slippage_bps(Side.BUY, 101, 100) == pytest.approx(100)
    assert slippage_bps(Side.BUY, 99, 100) == pytest.approx(-100)
    # sell below the benchmark = received less = positive
    assert slippage_bps(Side.SELL, 99, 100) == pytest.approx(100)
    assert slippage_bps(Side.SELL, 101, 100) == pytest.approx(-100)


def test_unfilled_shares_count_in_shortfall():
    bars = make_bars([(100, 100, 100, 100, 1000), (100, 110, 100, 110, 1000)])
    fills = [Fill(1, "X", Side.BUY, 50, 10000, bars.index[0])]
    r = analyze("t", "X", Side.BUY, 100, fills, bars)
    # 50 filled at arrival (no cost) + 50 unfilled that ran up to 110 -> 5% of the order
    assert r.shortfall_bps == pytest.approx(500)
    assert r.fill_rate == 0.5
    assert isinstance(r.to_dict(), dict) and "fill_rate" in r.to_dict()
    assert isinstance(pd.Series(r.to_dict()), pd.Series)
