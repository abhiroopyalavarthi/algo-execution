"""FixExchange against a running fix-gateway: same fills as SimExchange.

Skipped unless the quickfix package is installed and a gateway is listening
(FIX_GATEWAY=host:port, default 127.0.0.1:9878). To run it:
    (cd ../fix-gateway && ./build/fix_gateway &)
    uv sync --extra fix && uv run pytest tests/test_fix_exchange.py
"""

import os
import socket

import pytest

pytest.importorskip("quickfix")

HOST, _, PORT = os.environ.get("FIX_GATEWAY", "127.0.0.1:9878").partition(":")
PORT = int(PORT or 9878)


def _gateway_up() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _gateway_up(), reason=f"no FIX gateway at {HOST}:{PORT}")

from algo.core.backtester import run_backtest  # noqa: E402
from algo.core.exchange import SimExchange  # noqa: E402
from algo.core.orders import Side  # noqa: E402
from algo.strategies import TWAP, VWAP, MarketMaker, build_volume_curve  # noqa: E402


def _key(res):
    return [(f.order_id, f.qty, f.price, f.ts) for f in res.fills]


@pytest.mark.parametrize("name", ["vwap", "twap", "mm"])
def test_same_fills_through_fix(name, history, last_day, day_bars):
    from algo.core.fix_exchange import FixExchange

    curve = build_volume_curve(history, last_day)
    make = {
        "vwap": lambda: VWAP("SYN", Side.BUY, 300_000, curve=curve),
        "twap": lambda: TWAP("SYN", Side.SELL, 200_037),
        "mm": lambda: MarketMaker("SYN", half_spread_ticks=1, quote_size=300, max_position=1500),
    }[name]
    a = run_backtest({"SYN": day_bars}, make(), SimExchange())
    with FixExchange(host=HOST, port=PORT) as fx:
        b = run_backtest({"SYN": day_bars}, make(), fx)
    assert a.fills
    assert _key(a) == _key(b)
