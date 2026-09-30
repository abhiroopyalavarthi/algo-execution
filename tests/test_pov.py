from algo.core.backtester import run_backtest
from algo.core.orders import Side
from algo.strategies import POV

from .conftest import make_bars


def test_pov_never_exceeds_max_rate_even_in_a_quiet_bar():
    # busy, busy, then a bar with almost no volume, then busy again
    vols = [100_000, 100_000, 300, 100_000, 100_000]
    bars = make_bars([(10, 10.1, 9.9, 10, v) for v in vols])
    strat = POV("X", Side.BUY, 1_000_000, target_rate=0.10, max_rate=0.20, lot_size=1)
    res = run_backtest({"X": bars}, strat)
    per_bar = res.fills_frame().groupby("ts")["qty"].sum()
    for ts, q in per_bar.items():
        assert q <= 0.20 * bars.loc[ts, "volume"], ts
    # the quiet bar: the child was sized off 100k shares but only 60 can trade
    assert per_bar.get(bars.index[2], 0) <= 60


def test_pov_tracks_target_rate(day_bars):
    strat = POV("SYN", Side.BUY, 10_000_000, target_rate=0.05, max_rate=0.10)
    run_backtest({"SYN": day_bars}, strat)
    realized = strat.filled / day_bars["volume"].sum()
    assert 0.045 < realized < 0.055


def test_deadline_report_leaves_shares_unfilled(day_bars):
    qty = 50_000_000  # far more than 5% of the day
    strat = POV("SYN", Side.SELL, qty, target_rate=0.05, max_rate=0.10, on_deadline="report")
    res = run_backtest({"SYN": day_bars}, strat)
    assert 0 < strat.filled < qty
    assert strat.remaining > 0
    # nothing traded after the window
    assert max(f.ts for f in res.fills) <= day_bars.index[-1]


def test_deadline_sweep_tries_to_finish(day_bars):
    common = dict(target_rate=0.01, max_rate=0.02)
    qty = 2_000_000
    rep = POV("SYN", Side.BUY, qty, on_deadline="report", **common)
    swp = POV("SYN", Side.BUY, qty, on_deadline="sweep", **common)
    run_backtest({"SYN": day_bars}, rep)
    run_backtest({"SYN": day_bars}, swp)
    assert swp.filled > rep.filled
    last = day_bars.index[-1]
    assert swp.child_log[-1][0] == last
