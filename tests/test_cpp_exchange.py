"""SimExchange and CppExchange must give identical fills for the same orders."""

import pytest

pytest.importorskip("algo_cpp")

from algo.core.backtester import run_backtest  # noqa: E402
from algo.core.cpp_exchange import CppExchange  # noqa: E402
from algo.core.exchange import FillModel, SimExchange  # noqa: E402
from algo.core.orders import Order, OrderType, Side  # noqa: E402
from algo.data.loader import synthetic_pair  # noqa: E402
from algo.strategies import (  # noqa: E402
    POV,
    TWAP,
    VWAP,
    Aggressive,
    MarketMaker,
    PairsTrader,
    build_volume_curve,  # noqa: E402
)


def _key(res):
    return [(f.order_id, f.symbol, int(f.side), f.qty, f.price, f.ts) for f in res.fills]


def _both(make, data, model=None):
    a = run_backtest(data, make(), SimExchange(model))
    b = run_backtest(data, make(), CppExchange(model))
    return a, b


@pytest.mark.parametrize("name", ["twap", "vwap", "pov", "aggressive", "mm"])
def test_same_fills(name, history, last_day, day_bars):
    curve = build_volume_curve(history, last_day)
    makers = {
        "twap": lambda: TWAP("SYN", Side.BUY, 400_037),
        "vwap": lambda: VWAP("SYN", Side.SELL, 300_000, curve=curve),
        "pov": lambda: POV("SYN", Side.BUY, 3_000_000, target_rate=0.05, max_rate=0.08),
        "aggressive": lambda: Aggressive("SYN", Side.SELL, 20_000_000),
        "mm": lambda: MarketMaker("SYN", half_spread_ticks=1, quote_size=300,
                                  max_position=1500),
    }
    a, b = _both(makers[name], {"SYN": day_bars})
    assert a.fills, "no fills, test isn't testing anything"
    assert _key(a) == _key(b)


def test_same_fills_pairs():
    a_df, b_df = synthetic_pair()
    a, b = _both(lambda: PairsTrader("A", "B"), {"A": a_df, "B": b_df})
    assert a.fills and _key(a) == _key(b)


def test_same_fills_odd_model(day_bars):
    model = FillModel(max_participation=0.07, half_spread_ticks=3, depth_pct=0.013)
    a, b = _both(lambda: Aggressive("SYN", Side.BUY, 10_000_000), {"SYN": day_bars}, model)
    assert _key(a) == _key(b)


def test_same_fills_hand_written_sequence():
    """Several limit orders at a few prices, partial sweeps, cancels."""
    from algo.core.orders import Bar

    def drive(ex):
        out = []
        ex.set_fill_handler(lambda f: out.append((f.order_id, f.qty, f.price)))
        orders = [(1, Side.BUY, 998, 300), (2, Side.BUY, 999, 200), (3, Side.BUY, 998, 400),
                  (4, Side.SELL, 1003, 500), (5, Side.SELL, 1002, 100)]
        ex.process_bar(Bar.make(None, "X", 10, 10.01, 9.99, 10, 1_000))  # set up the book
        for oid, side, px, q in orders:
            ex.send_order(Order(oid, "X", side, q, OrderType.LIMIT, limit_px=px))
        ex.process_bar(Bar.make(None, "X", 10, 10.04, 9.97, 10, 2_400))  # cap 600 per side
        ex.cancel(3)
        ex.send_order(Order(6, "X", Side.BUY, 100, OrderType.LIMIT, limit_px=999))
        ex.process_bar(Bar.make(None, "X", 10, 10.00, 9.90, 9.95, 10_000))
        for o in list(ex.open_orders("X")):
            ex.cancel(o.id)
        ex.send_order(Order(7, "X", Side.BUY, 2_000))
        ex.send_order(Order(8, "X", Side.BUY, 900, max_pov=0.05))
        ex.process_bar(Bar.make(None, "X", 10, 10.10, 9.90, 10, 20_000))
        return out

    assert drive(SimExchange()) == drive(CppExchange())
