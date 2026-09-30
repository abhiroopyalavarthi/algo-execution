import pytest

from algo.core.backtester import run_backtest
from algo.core.exchange import FillModel, SimExchange
from algo.core.orders import Bar, Order, OrderType, Side
from algo.strategies import Aggressive

from .conftest import make_bars


def test_baseline_never_exceeds_volume_cap(day_bars):
    model = FillModel(max_participation=0.25)
    strat = Aggressive("SYN", Side.BUY, 5_000_000)
    res = run_backtest({"SYN": day_bars}, strat, SimExchange(model))
    per_bar = res.fills_frame().groupby("ts")["qty"].sum()
    vol = day_bars["volume"]
    for ts, q in per_bar.items():
        assert q <= vol[ts] * 0.25
    assert res.fills  # it did trade
    # and it takes the full cap on the first bar
    first = day_bars.index[0]
    assert per_bar[first] == model.cap(int(vol[first]))


def test_market_order_walks_the_book():
    ex = SimExchange(FillModel(max_participation=0.25, half_spread_ticks=1, depth_pct=0.05))
    fills = []
    ex.set_fill_handler(fills.append)
    ex.send_order(Order(1, "X", Side.BUY, 1200))
    bar = Bar.make(None, "X", 10.00, 10.05, 9.95, 10.00, 10_000)  # typical 1000, levels of 500
    ex.process_bar(bar)
    assert [(f.qty, f.price) for f in fills] == [(500, 1001), (500, 1002), (200, 1003)]


def test_market_remainder_is_dropped():
    ex = SimExchange(FillModel(max_participation=0.10))
    fills = []
    ex.set_fill_handler(fills.append)
    o = Order(1, "X", Side.SELL, 5_000)
    ex.send_order(o)
    ex.process_bar(Bar.make(None, "X", 10, 10.05, 9.95, 10, 10_000))
    assert sum(f.qty for f in fills) == 1_000
    assert not o.is_open
    ex.process_bar(Bar.make(None, "X", 10, 10.05, 9.95, 10, 10_000))
    assert sum(f.qty for f in fills) == 1_000


def test_limit_needs_trade_through():
    ex = SimExchange()
    fills = []
    ex.set_fill_handler(fills.append)
    ex.send_order(Order(1, "X", Side.BUY, 100, OrderType.LIMIT, limit_px=995))
    ex.process_bar(Bar.make(None, "X", 10, 10.05, 9.95, 10, 10_000))  # low == 9.95, only touches
    assert fills == []
    ex.process_bar(Bar.make(None, "X", 10, 10.05, 9.94, 10, 10_000))
    assert [(f.qty, f.price) for f in fills] == [(100, 995)]


def test_limit_priority_price_then_time():
    ex = SimExchange(FillModel(max_participation=0.03))  # cap = 300 of 10,000
    fills = []
    ex.set_fill_handler(fills.append)
    ex.send_order(Order(1, "X", Side.BUY, 200, OrderType.LIMIT, limit_px=998))
    ex.send_order(Order(2, "X", Side.BUY, 200, OrderType.LIMIT, limit_px=999))
    ex.send_order(Order(3, "X", Side.BUY, 200, OrderType.LIMIT, limit_px=998))
    ex.process_bar(Bar.make(None, "X", 10, 10.05, 9.90, 10, 10_000))
    assert [(f.order_id, f.qty) for f in fills] == [(2, 200), (1, 100)]


def _exchanges():
    out = [SimExchange]
    try:
        from algo.core.cpp_exchange import CppExchange
        out.append(CppExchange)
    except ImportError:
        pass
    return out


@pytest.mark.parametrize("cls", _exchanges())
def test_rejects_self_cross_and_mixing(cls):
    ex = cls()
    ex.process_bar(Bar.make(None, "X", 10, 10.05, 9.95, 10, 10_000))
    ex.send_order(Order(1, "X", Side.BUY, 100, OrderType.LIMIT, limit_px=1000))
    with pytest.raises(ValueError):
        ex.send_order(Order(2, "X", Side.SELL, 100, OrderType.LIMIT, limit_px=1000))
    with pytest.raises(ValueError):
        ex.send_order(Order(3, "X", Side.BUY, 100))


def test_orders_trade_on_the_next_bar():
    """A child sent in on_bar(t) must not trade in bar t."""
    bars = make_bars([(10, 10.1, 9.9, 10, 10_000)] * 3)
    strat = Aggressive("X", Side.BUY, 100)
    res = run_backtest({"X": bars}, strat)
    assert res.fills[0].ts == bars.index[0]  # sent in on_start -> first bar
    assert res.orders[0].ts is None
