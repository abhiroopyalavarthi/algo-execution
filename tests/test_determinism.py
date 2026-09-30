from algo.core.orders import Side
from algo.data.loader import synthetic_minute
from algo.runner import run_order


def _fills(seed):
    h = synthetic_minute("SYN", days=22, seed=seed)
    day = sorted(set(h.index.date))[-1]
    run = run_order("vwap", "SYN", day, 250_000, Side.SELL, h)
    return [(f.order_id, f.qty, f.price, f.ts) for f in run.result.fills], run.tca.to_dict()


def test_same_seed_same_fills():
    f1, t1 = _fills(5)
    f2, t2 = _fills(5)
    assert f1 == f2
    assert t1 == t2


def test_different_seed_different_fills():
    assert _fills(5)[0] != _fills(6)[0]
