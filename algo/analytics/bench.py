"""Backtest speed with the Python and C++ exchanges.

Two measurements:
  1. End to end: TWAP, VWAP and the market maker over N synthetic days, strategy
     code and event loop included. This is what a user of the backtester feels.
  2. Exchange only: a book-heavy workload (many resting limit orders per bar,
     cancels, sweeps, market orders) fed straight to the exchange, so the
     number reflects the matching work and not the Python strategy code.
"""

from __future__ import annotations

import platform
import time
import warnings
from pathlib import Path

import numpy as np

from algo.core.backtester import frame_to_bars, run_backtest
from algo.core.exchange import SimExchange
from algo.core.orders import Order, OrderType, Side
from algo.data.loader import synthetic_minute


def _exchanges():
    ex = {"python": SimExchange}
    try:
        from algo.core.cpp_exchange import CppExchange

        ex["cpp"] = CppExchange
    except ImportError:
        print("algo_cpp not built, benchmarking the Python exchange only")
    return ex


def end_to_end(days: int = 20, repeats: int = 3) -> dict:
    from algo.strategies import TWAP, VWAP, MarketMaker, build_volume_curve

    hist = synthetic_minute("SYN", days=days + 20, seed=1)
    all_days = sorted(set(hist.index.date))
    test_days = all_days[20:]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        curves = {d: build_volume_curve(hist, d) for d in test_days}
    day_frames = {d: hist[hist.index.date == d] for d in test_days}

    def jobs():
        for d in test_days:
            yield day_frames[d], lambda: TWAP("SYN", Side.BUY, 400_000)
            yield day_frames[d], lambda d=d: VWAP("SYN", Side.SELL, 400_000, curve=curves[d])
            yield day_frames[d], lambda: MarketMaker("SYN", half_spread_ticks=1, quote_size=200)

    out = {}
    for name, cls in _exchanges().items():
        best = float("inf")
        for _ in range(repeats):
            n = 0
            t0 = time.perf_counter()
            for frame, make in jobs():
                res = run_backtest({"SYN": frame}, make(), cls())
                n += res.n_bars
            best = min(best, time.perf_counter() - t0)
        out[name] = {"bars": n, "seconds": best, "bars_per_sec": n / best}
    return out


def exchange_only(n_bars: int = 2_000, orders_per_bar: int = 100, repeats: int = 3,
                  seed: int = 0) -> dict:
    """A book-heavy script fed straight to the exchange. Each bar: cancel our
    quotes that the new price would cross plus a random 20% of the rest, add
    `orders_per_bar` new quotes 1-200 ticks either side of the price (so a deep
    book builds up), and every 10th bar clear everything and send market orders."""
    hist = synthetic_minute("SYN", days=max(1, n_bars // 390 + 1), seed=seed)
    bars = frame_to_bars("SYN", hist)[:n_bars]
    rng = np.random.default_rng(seed)
    # pre-generate the whole script so both exchanges get identical input
    script = []
    oid = 1
    live: dict[int, tuple[Side, int]] = {}
    for i, bar in enumerate(bars):
        step = {"cancel": [], "new": []}
        mid = bar.close
        if i % 10 == 9:
            step["cancel"] = list(live)
            live = {}
            for _ in range(5):
                step["new"].append((oid, Side.BUY if rng.random() < 0.5 else Side.SELL,
                                    int(rng.integers(100, 5_000)), None))
                oid += 1
        else:
            for x, (side, px) in list(live.items()):
                crosses = px >= mid if side is Side.BUY else px <= mid
                if crosses or rng.random() < 0.2:
                    step["cancel"].append(x)
                    del live[x]
            for _ in range(orders_per_bar):
                side = Side.BUY if rng.random() < 0.5 else Side.SELL
                off = int(rng.integers(1, 200))
                px = mid - off if side is Side.BUY else mid + off
                step["new"].append((oid, side, int(rng.integers(1, 10)) * 100, px))
                live[oid] = (side, px)
                oid += 1
        script.append(step)
    n_orders = oid - 1

    out = {}
    for name, cls in _exchanges().items():
        best = float("inf")
        fills_seen = 0
        for _ in range(repeats):
            ex = cls()
            count = [0]
            ex.set_fill_handler(lambda f, c=count: c.__setitem__(0, c[0] + 1))
            t0 = time.perf_counter()
            for bar, step in zip(bars, script, strict=True):
                ex.process_bar(bar)  # fills for what was sent after the previous bar
                for x in step["cancel"]:
                    ex.cancel(x)
                for o_id, side, qty, px in step["new"]:
                    if px is None:
                        ex.send_order(Order(o_id, "SYN", side, qty))
                    else:
                        ex.send_order(Order(o_id, "SYN", side, qty, OrderType.LIMIT, limit_px=px))
            best = min(best, time.perf_counter() - t0)
            fills_seen = count[0]
        out[name] = {"bars": len(bars), "orders": n_orders, "fills": fills_seen,
                     "seconds": best, "bars_per_sec": len(bars) / best,
                     "orders_per_sec": n_orders / best}
    return out


def run_bench(repeats: int = 3, days: int = 20, out: Path | None = None) -> str:
    e2e = end_to_end(days, repeats)
    exo = exchange_only(repeats=repeats)
    lines = [f"machine: {platform.machine()} {platform.system()} {platform.release()}, "
             f"Python {platform.python_version()}", "",
             f"End to end (TWAP + VWAP + market maker, {days} days each, best of {repeats}):", "",
             "| exchange | bars | seconds | bars/sec |", "|---|---:|---:|---:|"]
    for k, v in e2e.items():
        lines.append(f"| {k} | {v['bars']:,} | {v['seconds']:.3f} | {v['bars_per_sec']:,.0f} |")
    if "cpp" in e2e:
        lines.append(f"\nC++ / Python: {e2e['cpp']['bars_per_sec'] / e2e['python']['bars_per_sec']:.2f}x")
    lines += ["", f"Exchange only (~{exo['python']['orders'] // exo['python']['bars']} orders "
              f"per bar, best of {repeats}):", "",
              "| exchange | bars | orders | fills | seconds | bars/sec | orders/sec |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for k, v in exo.items():
        lines.append(f"| {k} | {v['bars']:,} | {v['orders']:,} | {v['fills']:,} | "
                     f"{v['seconds']:.3f} | {v['bars_per_sec']:,.0f} | {v['orders_per_sec']:,.0f} |")
    if "cpp" in exo:
        lines.append(f"\nC++ / Python: {exo['cpp']['bars_per_sec'] / exo['python']['bars_per_sec']:.2f}x")
        assert exo["cpp"]["fills"] == exo["python"]["fills"], "exchanges disagree on fills"
    text = "\n".join(lines)
    print(text)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n")
    return text
