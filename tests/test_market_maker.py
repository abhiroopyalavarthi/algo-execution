import pytest

from algo.core.backtester import run_backtest
from algo.core.exchange import SimExchange
from algo.strategies import MarketMaker


@pytest.mark.parametrize("max_pos,size,skew", [(1000, 100, 1.0), (300, 200, 0.0), (500, 500, 2.0)])
def test_never_exceeds_max_position(day_bars, max_pos, size, skew):
    mm = MarketMaker("SYN", half_spread_ticks=1, quote_size=size, max_position=max_pos,
                     skew_ticks_per_lot=skew)
    res = run_backtest({"SYN": day_bars}, mm, SimExchange())
    pos, worst = 0, 0
    for f in res.fills:
        pos += f.side.sign * f.qty
        worst = max(worst, abs(pos))
    assert worst <= max_pos
    assert res.fills, "quoter never traded"


def test_flattens_at_the_end(day_bars):
    mm = MarketMaker("SYN", quote_size=100, max_position=1000)
    run_backtest({"SYN": day_bars}, mm)
    rep = mm.report()
    assert rep["final_position"] == 0
    assert 0 < rep["fill_rate"] <= 1
    assert len(rep["history"]) == len(day_bars)


def test_skew_pulls_inventory_back(day_bars):
    """With skew the average absolute inventory should be lower than without."""
    def avg_abs_inventory(skew):
        mm = MarketMaker("SYN", half_spread_ticks=2, quote_size=100, max_position=2000,
                         skew_ticks_per_lot=skew)
        run_backtest({"SYN": day_bars}, mm)
        return mm.report()["history"]["position"].abs().mean()

    assert avg_abs_inventory(2.0) < avg_abs_inventory(0.0)
