import math

from algo.core.backtester import run_backtest
from algo.data.loader import synthetic_pair
from algo.strategies import PairsTrader
from algo.strategies.pairs import rolling_beta


def test_rolling_beta_recovers_slope():
    import numpy as np
    rng = np.random.default_rng(1)
    b = np.cumsum(rng.normal(0, 0.01, 200))
    a = 0.7 * b + 0.3 + rng.normal(0, 0.001, 200)
    beta, alpha = rolling_beta(a, b)
    assert abs(beta - 0.7) < 0.02 and abs(alpha - 0.3) < 0.02


def test_pairs_runs_on_the_same_api():
    a, b = synthetic_pair()
    strat = PairsTrader("A", "B", lookback=60)
    res = run_backtest({"A": a, "B": b}, strat)
    rep = strat.report()
    assert rep["trades"] > 0
    assert math.isfinite(rep["sharpe"]) and rep["max_drawdown"] <= 0
    # on a mean-reverting synthetic spread it should make money
    assert rep["total_pnl"] > 0
    # it trades both legs
    assert {f.symbol for f in res.fills} == {"A", "B"}
